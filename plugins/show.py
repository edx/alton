import boto
import datetime
import logging
import os
import yaml
import requests
from itertools import izip_longest
from pprint import pformat
from will import settings
from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template

class TooManyImagesException(Exception):
    pass

class ShowPlugin(WillPlugin):

    @respond_to("show (?P<env>\w*)(-(?P<dep>\w*))(-(?P<play>\w*))?")
    def show(self, message, env, dep, play):
        """show <e-d-p>: show the instances in a VPC cluster"""
        if play == None:
            self.show_plays(message, env, dep)
        else:
            self.show_edp(message, env, dep, play)

    @respond_to("show (?P<deployment>\w*) (?P<ami_id>ami-\w*)")
    def show_ami(self, message, deployment, ami_id):
        """show deployment <ami_id>: show tags for the ami"""
        ec2 = boto.connect_ec2(profile_name=deployment)
        amis = ec2.get_all_images(ami_id)
        if len(amis) == 0:
            self.say("No ami found with id {}".format(ami_id), message)
        else:
            for ami in amis:
                self.say("/code {}".format(pformat(ami.tags)), message)

    def show_plays(self, message, env, dep):
        logging.info("Getting all plays in {}-{}".format(env,dep))
        ec2 = boto.connect_ec2(profile_name=dep)

        instance_filter = { "tag:environment": env, "tag:deployment": dep }
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

    def get_ami_for_edp(self, env, dep, play):
        ec2 = boto.connect_ec2(profile_name=dep)
        edp_filter = { "tag:environment" : env, "tag:deployment": dep, "tag:play": play }
        reservations = ec2.get_all_instances(filters=edp_filter)
        amis = set()
        for reservation in reservations:
            for instance in reservation.instances:
                if instance.state == 'running':
                    amis.add(instance.image_id)

        if len(amis) > 1:
            raise TooManyImagesException("Multiple AMIs found for EDP({}-{}-{})".format(env,dep,play))

        return amis.pop()
        
    def show_edp(self, message, env, dep, play):
        self.say("Reticulating splines...", message)
        ec2 = boto.connect_ec2(profile_name=dep)
        edp_filter = { "tag:environment" : env, "tag:deployment": dep, "tag:play": play }
        instances = ec2.get_all_instances(filters=edp_filter)

        output_table = [["Internal DNS", "Versions", "ELBs", "AMI"],
                        ["------------", "--------", "----", "---"],
                       ]
        instance_len, ref_len, elb_len, ami_len = map(len,output_table[0])

        for reservation in instances:
            for instance in reservation.instances:
                if instance.state != 'running':
                    continue
                logging.info("Getting info for: {}".format(instance.private_dns_name))
                refs = []
                ami_id = instance.image_id
                for ami in ec2.get_all_images(ami_id):
                    for name, value in ami.tags.items():
                        if name.startswith('version:'):
                            refs.append("{}={}".format(name[8:], value.split()[1]))

                elbs = map(lambda x: x.name, self.instance_elbs(instance.id, dep))

                for instance, ref, elb, ami in izip_longest(
                  [instance.private_dns_name],
                  refs,
                  elbs, [ami_id], fillvalue=""):
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
                line[3].ljust(ami_len),
                ))

        logging.error(output_table)
        self.say("/code {}".format("\n".join(output)), message)

    def get_ami_versions(self, profile, ami_id):
        versions_dict = {}
        ec2 = boto.connect_ec2(profile_name=profile)
        ami = ec2.get_all_images(ami_id)[0]
        # Build the versions_dict to have all versions defined in the ami tags
        for tag, value in ami.tags.items():
            if tag.startswith('version:'):
                key = tag[8:].strip()
                shorthash = value.split()[1]
                if key == 'configuration':
                    configuration_ref = shorthash
                elif key == 'configuration_secure':
                    configuration_secure_ref = shorthash
                else:
                    key = "{}_version".format(tag[8:])
                    # This is to deal with the fact that some
                    # versions are upper case and some are lower case.
                    versions_dict[key.lower()] = shorthash
                    versions_dict[key.upper()] = shorthash

        return (versions_dict, configuration_ref, configuration_secure_ref)


    @respond_to("(?P<noop>noop )?cut ami for (?P<env>\w*)-(?P<dep>\w*)-(?P<play>\w*)( from (?P<ami_id>ami-\w*))? with(?P<versions>( \w*=\S*)*)")
    def build_ami(self, message, env, dep, play, versions, ami_id=None, noop=False):
        """cut ami for: create a new ami from the given parameters"""
        versions_dict = {}
        configuration_ref="master"
        configuration_secure_ref="master"
        self.say("Let me get what I need to build the ami...", message)

        if ami_id:
            # Lookup the AMI and use its settings.
            self.say("Looking up ami {}".format(ami_id), message)
            ami_versions = self.get_ami_versions(dep, ami_id)
            versions_dict, configuration_ref, configuration_secure_ref = ami_versions

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

        self.notify_abbey(message, env, dep, play, versions_dict, configuration_ref, configuration_secure_ref, noop, ami_id)

    @respond_to("(?P<noop>noop )?update (?P<configuration>configuration )?(?P<configuration_secure>configuration_secure )?for (?P<env>\w*)-(?P<dep>\w*)-(?P<play>\w*)")
    def update_configuration(self, message, configuration, configuration_secure, env, dep, play, noop):
        running_ami = None
        try:
            running_ami = self.get_ami_for_edp(env,dep,play)
        except TooManyImagesException as e:
            msg = e.message
            msg += " Please resolve any running updates before building new AMIs."
            self.say(msg, message, color='red')
            return

        ami_versions = self.get_ami_versions(dep, running_ami)
        versions_dict, configuration_ref, configuration_secure_ref = ami_versions
        
        # Update configuration to master
        if configuration:
            configuration_ref = 'master'
        if configuration_secure:
            configuration_secure_ref = 'master'

        self.notify_abbey(message, env, dep, play, versions_dict, configuration_ref, configuration_secure_ref, noop, running_ami)

    def instance_elbs(self, instance_id, profile_name=None):
        elb = boto.connect_elb(profile_name=profile_name)
        elbs = elb.get_all_load_balancers()
        for lb in elbs:
            lb_instance_ids = [inst.id for inst in lb.instances]
            if instance_id in lb_instance_ids:
                yield lb

    def notify_abbey(self, message, env, dep, play, versions,
                     configuration_ref, configuration_secure_ref, noop=False, ami_id=None, verbose=False):

        if not hasattr(settings, 'JENKINS_URL'):
            self.say("The JENKINS_URL environment setting needs to be set so I can build AMIs.", message, color='red')
            return False
        else:
            abbey_url = settings.JENKINS_URL
            params = {}
            params['play'] = play
            params['deployment'] = dep
            params['environment'] = env
            params['vars'] = yaml.safe_dump(versions, default_flow_style=False)
            params['configuration'] = configuration_ref
            params['configuration_secure'] = configuration_secure_ref
            if ami_id:
                params['base_ami'] = ami_id
                params['use_blessed'] = False
            else:
                params['use_blessed'] = True

            logging.info("Need ami for {}".format(pformat(params)))

            output = "Building ami for {}-{}-{}\n".format(env, dep, play)
            if verbose:
                if ami_id:
                    output += "With base ami: {}\n".format(ami_id)
    
                display_params = dict(params)
                display_params['vars'] = versions
                output += yaml.safe_dump(
                    {"Params" : display_params },
                    default_flow_style=False)

            self.say(output, message)

            if noop:
                r = requests.Request('POST', abbey_url, params=params)
                url = r.prepare().url
                self.say("Would have posted: {}".format(url), message)
            else:
                r = requests.post(abbey_url, params=params)

                logging.info("Sent request got {}".format(r))
                message_color='green'
                if r.status_code != 200:
                    self.say("Sent request got {}".format(r),
                        message, color='red')
