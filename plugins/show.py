import boto
import datetime
import logging
from itertools import izip_longest
from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template

class ShowPlugin(WillPlugin):

    @respond_to("show (?P<env>\w*)-(?P<dep>\w*)-(?P<play>\w*)")
    def show(self, message, env, dep, play):
        output = ""
        ec2 = boto.connect_ec2()
        edp_filter = { "tag:environment" : env, "tag:deployment": dep, "tag:play": play }
        instances = ec2.get_all_instances(filters=edp_filter)

        output_table = [["Internal DNS", "Versions", "ELBs"],
                        ["------------", "--------", "----"],
                       ]
        instance_len, ref_len, elb_len = map(len,output_table[0])

        for reservation in instances:
            for instance in reservation.instances:
                logging.info("Getting info for: {}".format(instance.private_dns_name))
                refs = []
                for name, value in instance.tags.items():
                    if name.startswith('ref:'):
                        refs.append("{}: {}".format(name.lstrip('ref:'), value))

                elbs = map(lambda x: x.name, self.instance_elbs(instance.id))

                for instance, ref, elb in izip_longest(
                  [instance.private_dns_name],
                  refs,
                  elbs, fillvalue=""):
                    output_table.append([instance, ref, elb])
                    if instance:
                        instance_len = max(instance_len, len(instance))

                    if ref:
                        ref_len = max(ref_len, len(ref))

                    if elb:
                        elb_len = max(elb_len, len(elb))

        output = []
        for line in output_table:
            output.append("{} {} {}".format(line[0].ljust(instance_len),
                line[1].ljust(ref_len),
                line[2].ljust(elb_len),
                ))

        logging.error(output_table)
        self.say("/code {}".format("\n".join(output)), message)


    @respond_to("build-ami (?P<env>\w*)-(?P<dep>\w*)-(?P<play>\w*) ")
    def build_ami(self, message, env, dep, play):
        pass

    def instance_elbs(self, instance_id):
        elb = boto.connect_elb()
        elbs = elb.get_all_load_balancers()
        for lb in elbs:
            lb_instance_ids = [inst.id for inst in lb.instances]
            if instance_id in lb_instance_ids:
                yield lb
