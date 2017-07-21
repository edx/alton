"""
Show AWS data plugin
"""
import logging
import time
import urllib2
from itertools import izip_longest
from pprint import pformat
import jenkins
import yaml
from will import settings
from will.plugin import WillPlugin
from will.decorators import respond_to
import boto
from boto.exception import EC2ResponseError
from pyparsing import (
    Word, Combine, Suppress, OneOrMore, Optional, StringStart,
    StringEnd, alphanums, printables, Group, Regex, Literal, ParseException
)


class Versions(object):
    """
    Encapsulates versions associated with an AMI.
    """
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
    """
    Show plugin.
    """
    def __init__(self):
        if not hasattr(settings, "BOTO_PROFILES"):
            msg = "Error: BOTO_PROFILES not defined in the environment"
            self._say_error(msg)
        self.aws_profiles = settings.BOTO_PROFILES.split(";")  # pylint: disable=no-member

    @respond_to(r"^show (?!ami-)"  # Negative lookahead to exclude ami strings
                r"(?P<env>\w*)(-(?P<dep>\w*))(-(?P<play>\w*))?")
    def show(self, message, env, dep, play):
        """
        show [e-d-p]: show the instances in a VPC cluster
        """

        if play is None:
            self._show_plays(message, env, dep)
        else:
            self._show_edp(message, env, dep, play)

    @respond_to(r"^show (?P<deployment>\w*) (?P<ami_id>ami-\w*)")
    def show_ami_deprecated(self, message, deployment, ami_id):  # pylint: disable=unused-argument
        """
        show [deployment] [ami_id]: (DEPRECATED) show AMI.
        """
        self.say("This version of the command is deprecated. Please use the "
                 "format 'show {ami_id}'".format(ami_id=ami_id),
                 message=message, color='yellow')

    @respond_to(r"^show (?P<ami_id>ami-\w*)")
    def show_ami(self, message, ami_id):
        """
        show [ami_id]: show tags for the ami
        """

        ami = self._get_ami(ami_id, message=message)
        if ami:
            self.say("/code {}".format(pformat(ami.tags)), message)

    @respond_to(r"^diff "
                r"(?P<first_env>\w*)-"  # First Environment
                r"(?P<first_dep>\w*)-"  # First Deployment
                r"(?P<first_play>\w*)"  # First Play(Cluster)
                r" "
                r"(?P<second_env>\w*)-"  # Second Environment
                r"(?P<second_dep>\w*)-"  # Second Deployment
                r"(?P<second_play>\w*)")  # Second Play(Cluster)
    def diff_edps(self, message, first_env, first_dep, first_play,
                  second_env, second_dep, second_play):
        """
        diff [e-d-p] [e-d-p] : Show the differences between two EDPs
        """
        first_ami = self._ami_for_edp(
            message, first_env, first_dep, first_play)
        second_ami = self._ami_for_edp(
            message, second_env, second_dep, second_play)

        self._diff_amis(first_ami, second_ami, message)

    @respond_to(r"^diff "
                r"(?P<first_env>\w*)-"  # First Environment
                r"(?P<first_dep>\w*)-"  # First Deployment
                r"(?P<first_play>\w*)"  # First Play(Cluster)
                r" "
                r"(?P<second_ami>ami-\w*)")  # AMI
    def diff_edp_ami_id(self, message, first_env, first_dep, first_play,
                        second_ami):
        """
        diff [ami-id] [e-d-p] : Show the differences between an EDP and an AMI
        """
        first_ami = self._ami_for_edp(
            message, first_env, first_dep, first_play)
        self._diff_amis(first_ami, second_ami, message)

    @respond_to(r"^diff "
                r"(?P<first_ami>ami-\w*)"  # AMI
                r" "
                r"(?P<second_env>\w*)-"  # Second Environment
                r"(?P<second_dep>\w*)-"  # Second Deployment
                r"(?P<second_play>\w*)")  # Second Play(Cluster)
    def diff_ami_id_edp(self, message, first_ami,
                        second_env, second_dep, second_play):
        """
        diff [e-d-p] [ami-id] : Show the differences between an AMI and an EDP
        """
        second_ami = self._ami_for_edp(
            message, second_env, second_dep, second_play)
        self._diff_amis(first_ami, second_ami, message)

    @respond_to(r"^diff "
                r"(?P<first_ami>ami-\w*)"
                r" "
                r"(?P<second_ami>ami-\w*)")
    def diff_ami_ids(self, message, first_ami, second_ami):
        """
        diff [ami-id1] [ami-id2] : Show the difference between two AMIs
        """
        self._diff_amis(first_ami, second_ami, message)

    @respond_to(r"^(?P<body>cut\s+ami.*)")
    def cut_from_edp(self, message, body):
        """
        cut ami [noop] [verbose] for <e-d-c> from <e-d-c> [with <var1>=<value> <var2>=<version> ...] [using <ami-id>] :
            Build an AMI for one EDC using the versions from a different EDC with verions overrides
        """
        try:
            logging.info('Parsing: "{}"'.format(body))
            parsed = self._parse_cut_ami(body)
        except ParseException as exc:
            logging.info('Failed to parse cut-ami statement "{}": {}'.format(body, repr(exc)))
            self._say_error('Invalid syntax for "cut ami": ' + repr(exc), message=message)
            return

        dest_env, dest_dep, dest_play, source_env, source_dep, source_play = (
            parsed['dest_env'], parsed['dest_dep'], parsed['dest_play'],
            parsed['source_env'], parsed['source_dep'], parsed['source_play']
        )
        base_ami, version_overrides, verbose, noop = (
            parsed['base_ami'], parsed['version_overrides'], parsed['verbose'], parsed['noop']
        )

        # Get the active source AMI.
        self.say("Let me get what I need to build the ami...", message)

        if not all([source_env, source_dep, source_play]):
            # If the source is not specified use the destination
            # edp with overrides
            source_running_ami = self._ami_for_edp(
                message, dest_env, dest_dep, dest_play)

        else:
            source_running_ami = self._ami_for_edp(
                message, source_env, source_dep, source_play)

        if source_running_ami is None:
            return

        source_versions = self._get_ami_versions(source_running_ami,
                                                 message=message)

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

        # When building across deployments and not overriding configuration_secure.
        if dest_dep != source_dep and (
                version_overrides is None or "configuration_secure" not in version_overrides
        ):

            dest_versions = self._get_ami_versions(dest_running_ami,
                                                   message=message)
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
                    " with " + ' '.join('{}={}'.format(k, v) for k, v in version_overrides.items()) +
                    " configuration_secure=master")
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

        self._notify_abbey(message, dest_env, dest_dep, dest_play,
                           final_versions, noop, dest_running_ami, verbose)

    @staticmethod
    def _parse_cut_ami(text):
        """Parse "cut ami" command using pyparsing"""

        # Word == single token
        edctoken = Word(alphanums + '_')
        withtoken = Word(printables.replace('=', ''))

        preamble = Suppress(Literal('cut') + 'ami')

        # e.g. prod-edx-exdapp. Combining into 1 token enforces lack of whitespace
        e_d_c = Combine(edctoken('environment') + '-' + edctoken('deployment') + '-' + edctoken('cluster'))

        # e.g. cut ami for prod-edx-edxapp. Subsequent string literals are converted when added to a pyparsing object.
        for_from = Suppress('for') + e_d_c('for_edc') + Suppress('from') + e_d_c('from_edc')

        # e.g. with foo=bar bing=baz.
        # Group puts the k=v pairs in sublists instead of flattening them to the top-level token list.
        with_stmt = Suppress('with')
        with_stmt += OneOrMore(Group(withtoken('key') + Suppress('=') + withtoken('value')))('overrides')

        # e.g. using ami-deadbeef
        using_stmt = Suppress('using') + Regex('ami-[0-9a-f]{8}')('ami_id')

        # 0-1 with and using clauses in any order (see Each())
        modifiers = Optional(with_stmt('with_stmt')) & Optional(using_stmt('using_stmt'))

        # 0-1 verbose and noop options in any order (as above)
        options = Optional(Literal('verbose')('verbose')) & Optional(Literal('noop')('noop'))

        pattern = StringStart() + preamble + options + for_from + modifiers + StringEnd()

        parsed = pattern.parseString(text)
        return {
            'dest_env': parsed.for_edc.environment,
            'dest_dep': parsed.for_edc.deployment,
            'dest_play': parsed.for_edc.cluster,
            'source_env': parsed.from_edc.environment,
            'source_dep': parsed.from_edc.deployment,
            'source_play': parsed.from_edc.cluster,
            'base_ami': parsed.using_stmt.ami_id if parsed.using_stmt else None,
            'version_overrides': {i.key: i.value for i in parsed.with_stmt.overrides} if parsed.with_stmt else None,
            'verbose': bool(parsed.verbose),
            'noop': bool(parsed.noop),
        }

    def _show_plays(self, message, env, dep):
        """
        Gets all plays in an environment-deployment.
        """
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
        """
        Generator returning all ELBs.
        """
        if elbs is None:
            elb = boto.connect_elb(profile_name=profile_name)
            elbs = elb.get_all_load_balancers()

        for elb in elbs:
            lb_instance_ids = [inst.id for inst in elb.instances]
            if instance_id in lb_instance_ids:
                yield elb

    def _ami_for_edp(self, message, env, dep, play):
        """
        Given an EDP, return its active AMI.
        """
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
                if instance.state == 'running' and len(list(elbs)) > 0:  # pylint: disable=len-as-condition
                    amis.add(instance.image_id)

        if len(amis) > 1:
            msg = "Multiple AMIs found for {}-{}-{}, there should " \
                "be only one. Please resolve any running deploys " \
                "there before running this command."
            msg = msg.format(env, dep, play)
            self.say(msg, message, color='red')
            return None

        if len(amis) == 0:  # pylint: disable=len-as-condition
            msg = "No AMIs found for {}-{}-{}."
            msg = msg.format(env, dep, play)
            self.say(msg, message, color='red')
            return None

        return amis.pop()

    def _show_edp(self, message, env, dep, play):
        """
        Show info about a particular EDP.
        """
        self.say("Reticulating splines...", message)
        ec2 = boto.connect_ec2(profile_name=dep)
        elb = boto.connect_elb(profile_name=dep)
        edp_filter = {
            "tag:environment": env,
            "tag:deployment": dep,
            "tag:play": play,
        }
        instances = ec2.get_all_instances(filters=edp_filter)
        elbs = elb.get_all_load_balancers()
        if not instances:
            self.say('No instances found. The input may be misspelled.', message, color='red')
            return

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
                        key = name[8:]
                        if key == "configuration" or \
                           key == "configuration_secure" or \
                           key.endswith("_version") or \
                           key.endswith("_VERSION"):
                            refs.append(
                                "{}={}".format(key, value.split()[1]))
                        else:
                            refs.append(
                                "{}_version={}".format(key, value.split()[1]))

                elb_list = []
                for elb in elbs:
                    lb_instance_ids = [inst.id for inst in elb.instances]
                    if instance.id in lb_instance_ids:
                        elb_list.append(elb.name)

                all_data = izip_longest(
                    [instance.private_dns_name],
                    refs, elb_list, [ami_id],
                    fillvalue="",
                )
                for inst, ref, elb, ami in all_data:
                    output_table.append([inst, ref, elb, ami])
                    if inst:
                        instance_len = max(instance_len, len(inst))

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

        # Only make chunks of data exceeding the limit
        chunk_size = 65
        if len(output) > chunk_size:
            data = list(self._get_chunks(output, chunk_size))
            for chunk in data:
                self.say("/code {}".format("\n".join(chunk)), message)
        else:
            self.say("/code {}".format("\n".join(output)), message)
        logging.error(output_table)

    def _get_chunks(self, data, size):
        """
        Yields sized chunks for the data
        passed to it, issue related to Hiphcat
        limitation of not displaying a message
        having more than 10000 characters.
        """
        for items in range(0, len(data), size):
            yield data[items:items + size]

    def _get_ami_versions(self, ami_id, message=None):
        """
        Given an AMI, return the associated repo versions.
        """
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

        return Versions(
            configuration_ref,
            configuration_secure_ref,
            versions_dict,
            repos,
        )

    def _diff_url_from(self, first_data, second_data):
        """
        Diff repo URLs.
        """
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
        """
        Get the hash url from a repo.
        """
        url = "{}/tree/{}".format(
            self._web_url_from(repo_data),
            repo_data['shorthash']
        )
        return url

    def _web_url_from(self, repo_data):
        """
        Get the web url from a repo.
        """
        url = repo_data['url']
        # for both git and http links remove .git
        # so that /compare links work
        url = url.replace('.git', '')
        if url.startswith('git@'):
            url = url.replace(':', '/').replace('git@', 'http://')
        return url

    def _update_from_versions_string(self, defaults, versions, message):
        """Update with any version overrides defined in the versions_string."""
        if versions:
            for var, value in versions.items():
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
        """
        Interface with Abbey, where AMIs are built.
        """
        if not (
                hasattr(settings, 'JENKINS_URL') or
                hasattr(settings, 'JENKINS_API_KEY') or
                hasattr(settings, 'JENKINS_API_USER')
        ):
            msg = "The JENKINS_URL and JENKINS_API_KEY environment setting needs " \
                  "to be set so I can build AMIs."
            self.say(msg, message, color='red')
            return False
        else:
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
            params['jobid'] = '{}-{}-{}-{}-{}'.format(message.sender.nick, env, dep, play, int(time.time()))
            params['callback_url'] = settings.NOTIFY_CALLBACK_URL  # pylint: disable=no-member

            channel = self.get_room_from_message(message)['name']
            notification_list = {}
            notification_list[channel] = [message.sender.nick]
            self.save('notify_{}'.format(params['jobid']), notification_list, expire=259200)

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
                self.say("would have requested: {}".format(params), message)
            else:
                j = jenkins.Jenkins(
                    settings.JENKINS_URL, settings.JENKINS_API_USER, settings.JENKINS_API_KEY  # pylint: disable=no-member
                )
                jenkins_job_id = j.get_job_info('build-ami')['nextBuildNumber']
                self.say(
                    "starting job 'build-ami' Job number {}, build token {}".format(
                        jenkins_job_id, params['jobid']
                    ), message
                )
                try:
                    j.build_job('build-ami', parameters=params)
                except urllib2.HTTPError as exc:
                    self.say("Sent request got {}: {}".format(exc.code, exc.reason),
                             message, color='red')

    def _diff_amis(self, first_ami, second_ami, message):
        """
        Diff two AMIs to see repo differences.
        """
        first_ami_versions = self._get_ami_versions(first_ami, message=message)
        second_ami_versions = self._get_ami_versions(second_ami,
                                                     message=message)

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
            msg = ("Error: {num_amis} AMI(s) returned for {ami_id}, "
                   "for aws profiles {profiles}")
            self._say_error(msg.format(
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
