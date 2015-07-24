import boto
import logging
import threading
import time
import requests

from collections import namedtuple
from will import settings
from will.plugin import WillPlugin
from will.decorators import respond_to

from alton.ec2 import get_ami
from alton.user import requires_permission


class TimeoutException(Exception):
    pass

EDC = namedtuple('EDC', ['environment', 'deployment', 'cluster'])

class DeployPlugin(WillPlugin):
    # To keep track of vars specific to this run of the command.
    local = threading.local()
    aws_profiles = settings.BOTO_PROFILES.split(';')
    # http://18.176.5.232:8080/us-east-1/instance/list.json?asgardApiToken=

    def __init__(self):
        # Build URLS here
        self.BASE_URL = settings.ASGARD_API_ENDPOINT
        self.API_TOKEN={ "asgardApiToken" : settings.ASGARD_API_TOKEN }
        self.cluster_list_url= "{}/cluster/list.json".format(self.BASE_URL)
        # 'curl -d "name=helloworld-example-v004" http://asgardprod/us-east-1/cluster/activate'
        self.asg_activate_url= "{}/cluster/activate".format(self.BASE_URL)
        self.asg_deactivate_url= "{}/cluster/deactivate".format(self.BASE_URL)
        self.new_asg_url= "{}/cluster/createNextGroup".format(self.BASE_URL)
        self.asg_info_url="{}/autoScaling/show/{}.json".format(self.BASE_URL, "{}")

        # TODO: Fix this, formating to leave in some brackets?
        self.cluster_info_url = "{}/cluster/show/{}.json".format(self.BASE_URL, "{}")
        #name=helloworld-example&imageId=ami-40788629&trafficAllowed=false&checkHealth=true" 

    @respond_to("deploy\s+(?P<ami_id>ami-\w+)\s*$")
    @requires_permission("deploy")
    def deploy(self, message, ami_id):
        self.local.message = message
        self.reply(message, "Ok, here we go...")

        # Pull the EDC from the AMI ID
        # edc = self._edc_for(ami_id)
        #self.local.profile = edc.deployment

        # TODO: Remove this and restore the correct code
        # Just here for the demo.
        edc = EDC("test", "edx", "edxapp")
        self.local.profile = "sandbox"

        self.reply(message, "Looking for which clusters to deploy to.")
        asgs = self._asgs_for_edc(edc)

        # All the ASGs except for the new one
        # we are about to make.
        old_asgs = self._clusters_for_asgs(asgs)
        self.reply(message, "Deploying to {}".format(old_asgs.keys()))

        new_asgs = {}
        for cluster in old_asgs.keys():
            new_asgs[cluster] = self._new_asg(cluster, ami_id)

        self.reply(message, "New ASGs: {}".format(new_asgs.values()))
        self._wait_for_in_service(new_asgs.values(), 300)
        self.reply(message, "ASG instances are healthy. Enabling Traffic.")

        elbs_to_monitor = []
        for cluster, asg in new_asgs.iteritems():
            try:
                self._enable_asg(cluster, asg)        
                elbs_to_monitor.append(self._elbs_for_asg(asg))
            except:
                self.reply(message, "Something went wrong, disabling traffic.")
                self._disable_asg(asg)

        self.reply(message, "All new ASGs are active.  The new instances "
              "will be available when they pass the healthchecks.")

        # Wait for all instances to be in service in all ELBs
        try:
            self._wait_for_healthy_elbs(elbs_to_monitor, 600)
        except:
            self.reply(message, " Some instances are failing ELB health checks. "
                  "Pulling out the new ASG.")
            for cluster, asg in new_asgs.iteritems():
                self._disable_asg(asg)

        self.reply(message, "New instances have succeeded in passing the healthchecks. "
              "Disabling old ASGs.")
        for cluster,asg in old_asgs.iteritems():
            self._disable_asg(asg)

        self.reply(message, "Woot! Deploy Done!")

    def _edc_for(self, ami_id):
        logging.info("Looking up edc for {}".format(ami_id))
        tags = get_ami(ami_id, self.aws_profiles).tags

        cluster = None
        if 'cluster' in tags:
            cluster = tags['cluster']
        else:
            culster = tags['play']

        # TODO How do we want to handle these tags not existing?
        # raise an exception maybe? Right now this is not safe.
        return EDC(tags['environment'], tags['deployment'], cluster)

    def _asgs_for_edc(self, edc):
        """
        All AutoScalingGroups that have the tags of this cluster.

        A cluster is made up of many auto_scaling groups.
        """
        autoscale = boto.connect_autoscale(profile_name=self.local.profile)
        all_groups = autoscale.get_all_groups()
        for group in all_groups:
            tags = self._dict_from_tag_list(group.tags)
            print("Tags: {}".format(tags))
            if not tags:
                continue
            group_env = tags['environment']
            group_deployment = tags['deployment']
            if 'cluster' in tags:
                group_cluster = tags['cluster']
            else:
                group_cluster = tags['play']

            group_edc = EDC(group_env, group_deployment, group_cluster)
                         
            if group_edc == edc:
                yield group.name

    def _dict_from_tag_list(self, tag_list):
        tag_dict = {}
        for item in tag_list:
            tag_dict[item.key] = item.value

        return tag_dict
    def _clusters_for_asgs(self, asgs):
        # An autoscaling group can belong to multiple clusters potentially.
        # This function finds all asgard clusters for a list of ASGs.
        # eg. get all clusters that have the 'edxapp' cluster tag..

        # TODO: Can we cache this and do it less often?
        # response = request.get("http://admin-edx-hammer.edx.org/us-east-1/cluster/list.json")
        request = requests.Request('GET', self.cluster_list_url, params=self.API_TOKEN)
        url = request.prepare().url
        print("URL: {}".format(url))
        response = requests.get(self.cluster_list_url, params=self.API_TOKEN)
        cluster_json = response.json()
        # need this to be a list so that we can test membership.
        asgs = list(asgs) 

        relevant_clusters = {}
        for cluster in cluster_json:
            for asg in cluster['autoScalingGroups']:
                print("Membership: {} in {}: {}".format(asg, asgs, asg in asgs))
                if asg in asgs:
                    relevant_clusters[cluster['cluster']] = cluster['autoScalingGroups']
                    # A cluster can have multiple relevant ASGs.
                    # We don't need to check them all.
                    break # The inner for loop

        print("Relevant clusters we will deply to: {}".format(relevant_clusters))
        return relevant_clusters

    def _new_asg(self, cluster, ami_id):
        #'curl -d "name=helloworld-example&imageId=ami-40788629&trafficAllowed=false&checkHealth=true" http://asgardprod/us-east-1/cluster/createNextGroup'
        payload = {
            "name": cluster,
            "imageId": ami_id,
#            "trafficAllowed": False,
#            "checkHealth": True,
        }

        response = requests.post(self.new_asg_url, data=payload, params=self.API_TOKEN)
        print("Sent request.")

        self._wait_for_task_completion(response.url, 300)
        print("Task complete.")
       
        # Potential Race condition if multiple people are making ASGs for the same cluster
        # Return the name of the new asg
        return self._asgs_for_cluster(cluster)[-1]

    def _wait_for_in_service(self, all_asgs, timeout):
        """
        Wait for the ASG and all instances in it to be healthy
        accoding to AWS.
        """

        autoscale = boto.connect_autoscale(profile_name=self.local.profile)
        time_left = timeout
        asgs_left_to_check = list(all_asgs)
        print("ALL ASGs: {}".format(asgs_left_to_check))
        while time_left > 0:
            asgs = autoscale.get_all_groups(asgs_left_to_check)
            for asg in asgs:
                all_healthy = True
                for instance in asg.instances:
                    if instance.health_status != 'Healthy' and instance.lifecycle_state != 'InService':
                        # Instance is  not ready.
                        all_healthy = False
                        break

                if all_healthy:
                    # Then all are healthy we can stop checking this.
                    print("Removing asg: {}".format(asg.name))
                    asgs_left_to_check.remove(asg.name)

            if len(asgs_left_to_check) == 0:
                return

            time.sleep(1)
            time_left -= 1
        raise TimeoutException("Some instances in the following ASGs never became healthy: {}".format(asgs_left))

    def _enable_asg(self, cluster, asg):
        'curl -d "name=helloworld-example-v004" http://asgardprod/us-east-1/cluster/activate'
        payload = { "name": asg }
        response = requests.post(self.asg_activate_url, data=payload, params=self.API_TOKEN)
        task_url = response.url
        task_url = task_url.replace('localhost', '18.189.11.177') + '.json'

        self._wait_for_task_completion(task_url, 300)

    def _elbs_for_asg(self, asg):
        """
        curl http://admin-edx-hammer.edx.org/us-east-1/autoScaling/show/loadtest-edx-CommonClusterServerAsGroup-V9J31ZEID5C8-v001.json
        """
        response = requests.get(self.asg_info_url.format(asg), params=self.API_TOKEN)
        elbs = response.json()['group']['loadBalancerNames']
        return elbs

    def _asgs_for_cluster(self, cluster):
        """
        http://admin-edx-hammer.edx.org/us-east-1/cluster/show/loadtest-edx-notes.json
        """
        print("URL: {}".format(self.cluster_info_url.format(cluster)))
        response = requests.get(self.cluster_info_url.format(cluster), params=self.API_TOKEN)
        print("ASGs for Cluster: {}".format(response.text))
        asgs = response.json()
        asg_names = map(lambda x: x['autoScalingGroupName'], asgs)
        return asg_names

    def _wait_for_healthy_elbs(self, elbs_to_monitor, timeout):
        boto_elb = boto.connect_elb(profile_name=self.local.profile)
        time_left = timeout
        elbs_left = list(elbs_to_monitor)
        while time_left > 0:
            elbs = boto_elb.get_all_load_balancers(elbs_to_monitor)
            for elb in elbs:
                all_healthy = True
                for instance in elb.get_instance_health():
                    if instance.state != 'InService':
                        all_healthy = False
                        break

                if all_healthy:
                    elbs_left.remove(elb.name)

            if len(elbs_left) <= 0:
                return
            time.sleep(1)
            time_left -= 1

        raise TimeoutException("The following ELBs never became healthy: {}".format(elbs_left))

    def _disable_asg(self, asg):
        """
        curl -d "name=helloworld-example-v004" http://asgardprod/us-east-1/cluster/deactivate
        """
        payload = { "name": asg }
        response = requests.post(self.asg_deactivate_url, data=payload, params=self.API_TOKEN)
        task_url = response.url

        print("Disable ASG: Status URL: {}".format(task_url))
        
        self._wait_for_task_completion(task_url, 300)
       
    def _wait_for_task_completion(self, task_url, timeout):
        # TODO: we shouldn't have to do replace the host name but we will have to append '.json'
        task_url = task_url.replace('localhost', '18.176.5.222')

        if not task_url.endswith('.json'):
            task_url += ".json"

        print("URL: {}".format(task_url))
        time_left = timeout
        while time_left > 0:
            response = requests.get(task_url, params=self.API_TOKEN)
            print("Wait response: {}".format(response.text))
            status = response.json()['status']
            if status == 'completed':
                return
            time.sleep(1)
            time_left -= 1

        raise TimeoutException("Timedout while waiting for task {}".format(task_url))
