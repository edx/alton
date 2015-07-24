"""
Utility functions that have to do with getting
information about ec2 resources.  They will probably
mostly use boto to get usefule information but if there
are asgard utility functions they should also go here.
"""

def get_ami(ami_id, aws_profiles):
    """
    Looks for the given ami id accross all accounts
    Returns the AMI found
    """
    if not hasattr(settings, "BOTO_PROFILES"):
        msg = "Error: BOTO_PROFILES not defined in the environment"
        self._say_error(msg)
    aws_profiles = settings.BOTO_PROFILES.split(';')

    logging.info("looking up ami: {}".format(ami_id))
    found_amis = []
    for profile in aws_profiles:
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
        msg = msg.format(
            num_amis=len(found_amis),
            ami_id=ami_id,
            profiles='/'.join(aws_profiles))
        raise MultipleImagesException(msg)

    return found_amis[0]


