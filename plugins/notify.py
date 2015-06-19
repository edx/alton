from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template


class NotifyPlugin(WillPlugin):

    @route("/notify/<build_id>/<text>")
    def send_notification(self, build_id, text):
        if '@' in build_id:
            # We're using this to handle the edge case of a notification list that's passed in through jenkins instead of registered in alton.
            notification_list = build_id
            self.say("{} Message: {}".format(notification_list, text)) 
        else:
            notification_list = self.load("notify_{}".format(build_id), '')
        self.say("{} BuildID: {}, Message: {}".format(notification_list, build_id, text)) 

    @respond_to("^subscribe (@?)(?P<user>\S+) to (?P<build_id>\S+)")
    def subscribe(self, message, user, build_id):
        """
        subscribe [user] to [buildid]: request to be notified when builds complete
        """
        if user == "me":
            user = message.sender.nick

        notification_list = "{} @{}".format(
                self.load("notify_" + build_id, ''), 
                user)
        self.save('notify_{}'.format(build_id), notification_list, expire=259200)
        self.reply(message, "OK, I'll tell {} when I hear about {}".format(', '.join(notification_list.replace("@", "").strip().split(" ")), build_id))


