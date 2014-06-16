import boto
import datetime
import logging
import os
import yaml
import requests
from itertools import izip_longest
from pprint import pformat
from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template

class ShowPlugin(WillPlugin):

    @respond_to("show (?P<env>\w*)(-(?P<dep>\w*))(-(?P<play>\w*))?")
    def show(self, message, env, dep, play):
        """show <e-d-p>: show the instances in a VPC cluster"""
        if play == None:
            self.show_plays(message, env, dep)
        else:
            self.show_edp(message, env, dep, play)

    def show_plays(self, message, env, dep):
        logging.info("Getting all plays in {}-{}".format(env,dep))
        ec2 = boto.connect_ec2()
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

    def show_edp(self, message, env, dep, play):
        self.say("Reticulating splines...", message)
        ec2 = boto.connect_ec2()
        edp_filter = { "tag:environment" : env, "tag:deployment": dep, "tag:play": play }
        instances = ec2.get_all_instances(filters=edp_filter)

        output_table = [["Internal DNS", "Versions", "ELBs", "AMI"],
                        ["------------", "--------", "----", "---"],
                       ]
        instance_len, ref_len, elb_len, ami_len = map(len,output_table[0])

        for reservation in instances:
            for instance in reservation.instances:
                logging.info("Getting info for: {}".format(instance.private_dns_name))
                refs = []
                ami_id = instance.image_id
                for ami in ec2.get_all_images(ami_id):
                    for name, value in ami.tags.items():
                        if name.startswith('refs:'):
                            refs.append("{}: {}".format(name[5:], value))

                        if name.endswith("_ref"):
                            refs.append("{}: {}".format(name, value))

                elbs = map(lambda x: x.name, self.instance_elbs(instance.id))

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


    @respond_to("(noop )?cut ami for (?P<env>\w*)-(?P<dep>\w*)-(?P<play>\w*)( from (?P<ami_id>ami-\w*))? with(?P<versions>( \w*=\w*)*)")
    def build_ami(self, message, env, dep, play, versions, ami_id=None, noop=False):
        """cut ami for: create a new ami from the given parameters"""
        versions_dict = {}
        configuration_ref=""
        configuration_secure_ref=""
        self.say("Let me get what I need to build the ami...", message)

        if ami_id:
            self.say("Looking up ami {}".format(ami_id), message)
            ec2 = boto.connect_ec2()
            edp_filter = { "tag:environment" : env, "tag:deployment": dep, "tag:play": play }
            ec2 = boto.connect_ec2()
            ami = ec2.get_all_images(ami_di)[0]
            for tag, value in ami.tags.items():
                if tag.startswith('refs:'):
                    key = tag[5:]
                    versions_dict[key] = value

                if tag == 'configuration_ref':
                    configuration_ref = value
                if tag == 'configuration_secure_ref':
                    configuration_secure_ref = value

        output = "Building ami for {}-{}-{} with base ami {} and versions:\n".format(env, dep, play, ami_id)
        for version in versions.split():
            var, value = version.split('=')
            versions_dict[var] = value
            output += "{}: {}\n".format(var, value)

        # Lookup the AMI and use its settings.

        logging.info("Notifying Abbey...")
        self.notify_abbey(message, env, dep, play, versions_dict, configuration_ref, configuration_secure_ref, noop)
        self.say(output, message)

    def instance_elbs(self, instance_id):
        elb = boto.connect_elb()
        elbs = elb.get_all_load_balancers()
        for lb in elbs:
            lb_instance_ids = [inst.id for inst in lb.instances]
            if instance_id in lb_instance_ids:
                yield lb

    def notify_abbey(self, message, env, dep, play, versions,
                     configuration_ref, configuration_secure_ref, noop=False):
        abbey_url = os.getenv('JENKINS_URL', 'http://10.254.2.119:8080/buildByToken/buildWithParameters?job=build-ami-copy&token=MULTIPASS')

        if True:
            params = {}
            params['play'] = play
            params['deployment'] = dep
            params['environment'] = env
            params['refs'] = yaml.safe_dump(versions, default_flow_style=False)
            params['configuration'] = configuration_ref
            params['configuration_secure'] = configuration_secure_ref
            params['use_blessed'] = True

            logging.info("Need ami for {}".format(pformat(params)))
            if noop:
                r = requests.Request('POST', abbey_url, params=params)
                url = r.prepare().url
                self.say("Would have posted: {}".format(url), message)
            else:
                r = requests.post(abbey_url, params=params)

                logging.info("Sent request got {}".format(r))
                self.say("Sent request got {}".format(r), message)
                if r.status_code != 200:
                    # Something went wrong.
                    msg = "Failed to submit request with params: \n{}"
                    self.say(msg.format(pformat(params)), message, color="red")

