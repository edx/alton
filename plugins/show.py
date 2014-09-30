import boto
import logging
import yaml
import requests
from itertools import izip_longest
from pprint import pformat
from will import settings
from will.plugin import WillPlugin
from will.decorators import respond_to
from boto.exception import EC2ResponseError


class Versions():

    def __init__(self, configuration_ref, configuration_secure_ref, versions,
                 repos=None):
        """
        configurations_ref: The gitref for configurations.
        configuration_secure_ref: The git ref for configuration_secure
        versions: A dict mapping version vars('.*_version') to gitrefs.
        """
        self.configuration = configuration_ref
        self.configuration_secure = configuration_secure_ref
        self.play_versions = versions
        self.repos = repos


class ShowPlugin(WillPlugin):

    def __init__(self):
        if not hasattr(settings, "WILL_BOTO_PROFILES"):
            self._say_error("Error: WILL_BOTO_PROFILES not defined in the environment")
        self.aws_profiles = settings.WILL_BOTO_PROFILES.split(";")

    @respond_to("^show (?!ami-)"  # Negative lookahead to exclude ami strings
                "(?P<env>\w*)(-(?P<dep>\w*))(-(?P<play>\w*))?")
    def show(self, message, env, dep, play):
        """show <e-d-p>: show the instances in a VPC cluster"""
        if play is None:
            self._show_plays(message, env, dep)
        else:
            self._show_edp(message, env, dep, play)

    @respond_to("^show (?P<deployment>\w*) (?P<ami_id>ami-\w*)")
    def show_ami_deprecated(self, message, deployment, ami_id):
        self.say("This version of the command is deprecated. Please use the "
                 "format 'show {ami_id}'".format(ami_id=ami_id),
                 message=message, color='yellow')

    @respond_to("^show (?P<ami_id>ami-\w*)")
    def show_ami(self, message, ami_id):
        """show deployment <ami_id>: show tags for the ami"""
        ami = self._get_ami(ami_id, message=message)
        if ami:
            self.say("/code {}".format(pformat(ami.tags)), message)

    @respond_to("(?P<noop>noop )?cut ami for "  # Initial words
                "(?P<env>\w*)-(?P<dep>\w*)-(?P<play>\w*)"  # Get the EDP
                "( from (?P<ami_id>ami-\w*))? "  # Optionally provide an ami
                "with(?P<versions>( \w*=\S*)*)")  # Override versions
    def build_ami(self, message, env, dep, play, versions,
                  ami_id=None, noop=False):
        """cut ami for: create a new ami from the given parameters"""

        self.say("This version of the command is deprecated. Please use the "
                 "format 'cut ami for <e-d-c> from "
                 "<e-d-c> [using ami-???] [with [var=version]...]'",
                 message=message, color='yellow')

        versions_dict = {}
        configuration_ref = None
        configuration_secure_ref = None
        self.say("Let me get what I need to build the ami...", message)

        if ami_id:
            # Lookup the AMI and use its settings.
            self.say("Looking up ami {}".format(ami_id), message)
            ami_versions = self._get_ami_versions(ami_id, message=message)
            if not ami_versions:
                return
            configuration_ref = ami_versions.configuration
            configuration_secure_ref = ami_versions.configuration_secure
            versions_dict = ami_versions.play_versions

        if configuration_ref is None:
            configuration_ref = "master"
        if configuration_secure_ref is None:
            configuration_secure_ref = "master"

        # Override the ami and defaults with the setting from the user
        for version in versions.split():
            var, value = version.split('=')
            if var == 'configuration':
                configuration_ref = value
            elif var == 'configuration_secure':
                configuration_secure_ref = value
            else:
                versions_dict[var.lower()] = value
                versions_dict[var.upper()] = value

        final_versions = Versions(
            configuration_ref,
            configuration_secure_ref,
            versions_dict)

        self.notify_abbey(
            message, env, dep, play, final_versions, noop, ami_id)

    @respond_to("^diff "
                "(?P<first_env>\w*)-"  # First Environment
                "(?P<first_dep>\w*)-"  # First Deployment
                "(?P<first_play>\w*)"  # First Play(Cluster)
                " "
                "(?P<second_env>\w*)-"  # Second Environment
                "(?P<second_dep>\w*)-"  # Second Deployment
                "(?P<second_play>\w*)")  # Second Play(Cluster)
    def diff_edps(self, message, first_env, first_dep, first_play,
                  second_env, second_dep, second_play):
        first_ami = self._ami_for_edp(
            message, first_env, first_dep, first_play)
        second_ami = self._ami_for_edp(
            message, second_env, second_dep, second_play)

        self._diff_amis(first_ami, second_ami, message)

    @respond_to("^diff "
                "(?P<first_env>\w*)-"  # First Environment
                "(?P<first_dep>\w*)-"  # First Deployment
                "(?P<first_play>\w*)"  # First Play(Cluster)
                " "
                "(?P<second_ami>ami-\w*)")  # AMI
    def diff_edp_ami_id(self, message, first_env, first_dep, first_play, second_ami):
        first_ami = self._ami_for_edp(
            message, first_env, first_dep, first_play)
        self._diff_amis(first_ami, second_ami, message)

    @respond_to("^diff "
                "(?P<first_ami>ami-\w*)"  # AMI
                " "
                "(?P<second_env>\w*)-"  # Second Environment
                "(?P<second_dep>\w*)-"  # Second Deployment
                "(?P<second_play>\w*)")  # Second Play(Cluster)
    def diff_ami_id_edp(self, message, first_ami, second_env, second_dep, second_play):
        second_ami = self._ami_for_edp(
            message, second_env, second_dep, second_play)
        self._diff_amis(first_ami, second_ami, message)

    @respond_to("^diff "
                "(?P<first_ami>ami-\w*)"
                " "
                "(?P<second_ami>ami-\w*)")
    def diff_ami_ids(self, message, first_ami, second_ami):
        self._diff_amis(first_ami, second_ami, message)

    # A regex to build an AMI for one EDP from another EDP.
    @respond_to("(?P<verbose>verbose )?(?P<noop>noop )?cut ami for "  # Options
                "(?P<dest_env>\w*)-"  # Destination Environment
                "(?P<dest_dep>\w*)-"  # Destination Deployment
                "(?P<dest_play>\w*) "  # Destination Play(Cluster)
                "from "
                "(?P<source_env>\w*)-"  # Source Environment
                "(?P<source_dep>\w*)-"  # Source Deployment
                "(?P<source_play>\w*)"  # Source Play(Cluster)
                "( using (?P<base_ami>ami-\w*))?"
                "( with(?P<version_overrides>( \w*=\S*)*))?")  # Overrides
    def cut_from_edp(self, message, verbose, noop, dest_env, dest_dep,
                     dest_play, source_env, source_dep, source_play, base_ami,
                     version_overrides):
        # Get the active source AMI.
        self.say("Let me get what I need to build the ami...", message)
        source_running_ami = self._ami_for_edp(
            message, source_env, source_dep, source_play)
        if source_running_ami is None:
            return

        source_versions = self._get_ami_versions(source_running_ami, message=message)

        if not source_versions:
            return

        # Use the base ami if provided.
        if base_ami is not None:
            self.say("Using {} as base-ami.".format(base_ami), message)
            dest_running_ami = base_ami
        else:
            # Get the active destination AMI.  The one we're gonna
            # use as a base for our build.
            dest_running_ami = self._ami_for_edp(
                message, dest_env, dest_dep, dest_play)
            if dest_running_ami is None:
                return

        final_versions = self._update_from_versions_string(
            source_versions, version_overrides, message)

        # When building accross deployments and not overriding
        # configuration_secure.
        if dest_dep != source_dep \
           and (version_overrides is None
                or "configuration_secure" not in version_overrides):

            dest_versions = self._get_ami_versions(dest_running_ami, message=message)
            if not dest_versions:
                return

            final_versions.configuration_secure = \
                dest_versions.configuration_secure

            msg = ("Warning: {dest_dep} uses a different repository for "
                   "tracking secrets so the version of configuration_secure "
                   "from {source_env}-{source_dep} doesn't make sense to use "
                   "for the {dest_dep} AMI.  The new {dest_dep} AMI will "
                   "not override the current version of configuration_secure "
                   "on {dest_env}-{dest_dep}-{dest_play}({dest_secure_ref}). "
                   "If you would like to update the secrets on {dest_dep} "
                   "you should build a new ami where you override "
                   "configuration_secure.\n"
                   "For example:")
            msg = msg.format(
                dest_env=dest_env,
                dest_dep=dest_dep,
                dest_play=dest_play,
                source_env=source_env,
                source_dep=source_dep,
                dest_secure_ref=dest_versions.configuration_secure,
            )
            example_command = (
                "/code cut ami for {dest_env}-{dest_dep}-{dest_play} "
                "from {source_env}-{source_dep}-{source_play}")
            if version_overrides:
                example_command += (
                    " with " + version_overrides +
                    "configuration_secure=master")
            else:
                example_command += " with configuration_secure=master"
            example_command = example_command.format(
                dest_env=dest_env,
                dest_dep=dest_dep,
                dest_play=dest_play,
                source_env=source_env,
                source_dep=source_dep,
                source_play=source_play,
            )

            self.say(msg, message=message, color='yellow')
            self.say(example_command, message=message, color='yellow')

        self.notify_abbey(message, dest_env, dest_dep, dest_play,
                          final_versions, noop, dest_running_ami, verbose)

    def _show_plays(self, message, env, dep):
        logging.info("Getting all plays in {}-{}".format(env, dep))
        ec2 = boto.connect_ec2(profile_name=dep)

        instance_filter = {
            "tag:environment": env,
            "tag:deployment": dep,
        }
        instances = ec2.get_all_instances(filters=instance_filter)

        plays = set()
        for reservation in instances:
            for instance in reservation.instances:
                if "play" in instance.tags:
                    play_name = instance.tags["play"]
                    plays.add(play_name)

        output = ["Active Plays",
                  "------------"]
        output.extend(list(plays))
        self.say("/code {}".format("\n".join(output)), message)

    def _instance_elbs(self, instance_id, profile_name=None, elbs=None):

        if elbs is None:
            elb = boto.connect_elb(profile_name=profile_name)
            elbs = elb.get_all_load_balancers()

        for lb in elbs:
            lb_instance_ids = [inst.id for inst in lb.instances]
            if instance_id in lb_instance_ids:
                yield lb

    def _ami_for_edp(self, message, env, dep, play):

        ec2 = boto.connect_ec2(profile_name=dep)
        elb = boto.connect_elb(profile_name=dep)
        all_elbs = elb.get_all_load_balancers()

        edp_filter = {
            "tag:environment": env,
            "tag:deployment": dep,
            "tag:play": play,
        }
        reservations = ec2.get_all_instances(filters=edp_filter)
        amis = set()
        for reservation in reservations:
            for instance in reservation.instances:
                elbs = self._instance_elbs(instance.id, dep, all_elbs)
                if instance.state == 'running' and len(list(elbs)) > 0:
                    amis.add(instance.image_id)

        if len(amis) > 1:
            msg = "Multiple AMIs found for {}-{}-{}, there should " \
                "be only one. Please resolve any running deploys " \
                "there before running this command."
            msg = msg.format(env, dep, play)
            self.say(msg, message, color='red')
            return None

        if len(amis) == 0:
            msg = "No AMIs found for {}-{}-{}."
            msg = msg.format(env, dep, play)
            self.say(msg, message, color='red')
            return None

        return amis.pop()

    def _show_edp(self, message, env, dep, play):
        self.say("Reticulating splines...", message)
        ec2 = boto.connect_ec2(profile_name=dep)
        edp_filter = {
            "tag:environment": env,
            "tag:deployment": dep,
            "tag:play": play,
        }
        instances = ec2.get_all_instances(filters=edp_filter)

        output_table = [
            ["Internal DNS", "Versions", "ELBs", "AMI"],
            ["------------", "--------", "----", "---"],
        ]
        instance_len, ref_len, elb_len, ami_len = map(len, output_table[0])

        for reservation in instances:
            for instance in reservation.instances:
                if instance.state != 'running':
                    continue
                msg = "Getting info for: {}"
                logging.info(msg.format(instance.private_dns_name))
                refs = []
                ami_id = instance.image_id
                ami = self._get_ami(ami_id, message=message)
                if not ami:
                    return None
                for name, value in ami.tags.items():
                    if name.startswith('version:'):
                        refs.append(
                            "{}={}".format(name[8:], value.split()[1]))

                instance_name = lambda x: x.name
                elbs = map(instance_name,
                           self._instance_elbs(instance.id, dep))

                all_data = izip_longest(
                    [instance.private_dns_name],
                    refs, elbs, [ami_id],
                    fillvalue="",
                )
                for instance, ref, elb, ami in all_data:
                    output_table.append([instance, ref, elb, ami])
                    if instance:
                        instance_len = max(instance_len, len(instance))

                    if ref:
                        ref_len = max(ref_len, len(ref))

                    if elb:
                        elb_len = max(elb_len, len(elb))

                    if ami:
                        ami_len = max(ami_len, len(ami))

        output = []
        for line in output_table:
            output.append("{} {} {} {}".format(line[0].ljust(instance_len),
                                               line[1].ljust(ref_len),
                                               line[2].ljust(elb_len),
                                               line[3].ljust(ami_len),))

        logging.error(output_table)
        self.say("/code {}".format("\n".join(output)), message)

    def _get_ami_versions(self, ami_id, message=None):
        versions_dict = {}
        ami = self._get_ami(ami_id, message=message)
        if not ami:
            return None
        configuration_ref = None
        configuration_secure_ref = None
        repos = {}
        # Build the versions_dict to have all versions defined in the ami tags
        for tag, value in ami.tags.items():
            if tag.startswith('version:'):
                key = tag[8:].strip()
                repo, shorthash = value.split()
                repos[key] = {
                    'url': repo,
                    'shorthash': shorthash
                }

                if key == 'configuration':
                    configuration_ref = shorthash
                elif key == 'configuration_secure':
                    configuration_secure_ref = shorthash
                else:
                    key = "{}_version".format(key)
                    # This is to deal with the fact that some
                    # versions are upper case and some are lower case.
                    versions_dict[key.lower()] = shorthash
                    versions_dict[key.upper()] = shorthash

        return Versions(configuration_ref,
                        configuration_secure_ref,
                        versions_dict,
                        repos,
                        )

    def _diff_url_from(self, first_data, second_data):
        if first_data['url'] != second_data['url']:
            msg = "clusters use different repos for this: {} vs {}".format(
                self._hash_url_from(first_data),
                self._hash_url_from(second_data))
            return msg

        if first_data['shorthash'] == second_data['shorthash']:
            msg = "no difference"
            return msg

        url = "{}/compare/{}...{}".format(
            self._web_url_from(first_data),
            first_data['shorthash'],
            second_data['shorthash']
        )

        return url

    def _hash_url_from(self, repo_data):
        url = "{}/tree/{}".format(
            self._web_url_from(repo_data),
            repo_data['shorthash']
        )
        return url

    def _web_url_from(self, repo_data):
        if repo_data['url'].startswith('git@'):
            url = repo_data['url'].replace(':', '/')
            url = url.replace('.git', '')
            url = url.replace('git@', 'http://')

            return url
        else:
            return repo_data['url']

    def _update_from_versions_string(self, defaults, versions_string, message):
        """Update with any version overrides defined in the versions_string."""
        if versions_string:
            for version in versions_string.split():
                var, value = version.split('=')
                msg = "Overriding '{}' for the new AMI."
                self.say(msg.format(var), message)
                if var == 'configuration':
                    defaults.configuration = value
                elif var == 'configuration_secure':
                    defaults.configuration_secure = value
                else:
                    defaults.play_versions[var.lower()] = value
                    defaults.play_versions[var.upper()] = value
        return defaults

    def _notify_abbey(self, message, env, dep, play, versions,
                      noop=False, ami_id=None, verbose=False):

        if not hasattr(settings, 'JENKINS_URL'):
            msg = "The JENKINS_URL environment setting needs " \
                  "to be set so I can build AMIs."
            self.say(msg, message, color='red')
            return False
        else:
            abbey_url = settings.JENKINS_URL
            play_vars = yaml.safe_dump(
                versions.play_versions,
                default_flow_style=False,
            )
            params = {}
            params['play'] = play
            params['deployment'] = dep
            params['environment'] = env
            params['vars'] = play_vars
            params['configuration'] = versions.configuration
            params['configuration_secure'] = versions.configuration_secure
            if ami_id:
                params['base_ami'] = ami_id
                params['use_blessed'] = False
            else:
                params['use_blessed'] = True

            logging.info("Need ami for {}".format(pformat(params)))

            output = "Building ami for {}-{}-{}\n".format(env, dep, play)
            if verbose:
                display_params = dict(params)
                display_params['vars'] = versions.play_versions
                output += yaml.safe_dump(
                    {"Params": display_params},
                    default_flow_style=False)

            self.say(output, message)

            if noop:
                r = requests.Request('POST', abbey_url, params=params)
                url = r.prepare().url
                self.say("Would have posted: {}".format(url), message)
            else:
                r = requests.post(abbey_url, params=params)

                logging.info("Sent request got {}".format(r))
                if r.status_code != 200:
                    self.say("Sent request got {}".format(r),
                             message, color='red')

    def _diff_amis(self, first_ami, second_ami, message):

        first_ami_versions = self._get_ami_versions(first_ami, message=message)
        second_ami_versions = self._get_ami_versions(second_ami, message=message)

        if not first_ami_versions or not second_ami_versions:
            return None

        first_versions = first_ami_versions.repos
        second_versions = second_ami_versions.repos

        diff_urls = {}
        repos_added = {}
        repos_removed = {}
        for repo_name, repo_data in first_versions.items():
            if repo_name in second_versions:
                diff_urls[repo_name] = \
                    self._diff_url_from(repo_data, second_versions[repo_name])
            else:
                repos_added[repo_name] = self._hash_url_from(repo_data)

        for repo_name, repo_data in second_versions.items():
            if repo_name in first_versions:
                if repo_name not in diff_urls:
                    diff_urls[repo_name] = self._diff_url_from(
                        first_versions[repo_name], repo_data)
            else:
                repos_removed[repo_name] = self._hash_url_from(repo_data)

        msgs = []
        for repo_name, url in diff_urls.items():
            msgs.append("{}: {}".format(repo_name, url))

        for repo_name, url in repos_added.items():
            msgs.append("Added {}: {}".format(repo_name, url))

        for repo_name, url in repos_removed.items():
            msgs.append("Removed {}: {}".format(repo_name, url))

        for line in msgs:
            self.say(line, message)

    def _get_ami(self, ami_id, message=None):
        """
        Looks for the given ami id accross all accounts
        Returns the AMI found
        """
        logging.info("looking up ami: {}".format(ami_id))
        found_amis = []
        for profile in self.aws_profiles:
            ec2 = boto.connect_ec2(profile_name=profile)
            try:
                images = ec2.get_all_images(ami_id)
            except EC2ResponseError:
                # failures expected for other accounts
                images = []
            found_amis.extend(images)
        if len(found_amis) != 1:
            self._say_error("Error: {num_amis} AMI(s) returned for {ami_id}, for aws profiles {profiles}".format(
                num_amis=len(found_amis),
                ami_id=ami_id,
                profiles='/'.join(self.aws_profiles)), message=message)
            return None
        return found_amis[0]

    def _say_error(self, msg, message=None):
        """
        Reports an error
        """
        self.say(msg, message=message, color="red")
