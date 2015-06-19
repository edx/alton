from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template


class NotifyPlugin(WillPlugin):

    @route("/notify/<build_id>/<text>")
    def send_notification(self, build_id, text):
        if '@' in build_id:
            # We're using this to handle the edge case of a notification list that's passed in through jenkins instead of registered in alton.
            notification_list = build_id
            self.say("{} Message: {}".format(notification_list, text), notify=True) 
        else:
            notification_list = self.load("notify_" + build_id, {})
            for room in notification_list:
                self.say("{} BuildID: {}, Message: {}".format(notification_list[room], build_id, text), room=self.get_room_from_name_or_id(room), notify=True) 

    @respond_to("^subscribe (@?)(?P<user>\S+) to (?P<build_id>\S+)")
    def subscribe(self, message, user, build_id):
        """
        subscribe [user] to [buildid]: request to be notified when builds complete
        """
        if user == "me":
            user = message.sender.nick
        channel = self.get_room_from_message(message)['name']
        notification_list = self.load("notify_" + build_id, {})
        notification_list[channel] = "{} @{}".format(notification_list.get(channel, ""), user)

        self.save('notify_{}'.format(build_id), notification_list, expire=259200)
        self.reply(message, "OK, I'll tell {} when I hear about {}".format(', '.join(
            notification_list[channel].replace("@", "").strip().split(" ")), 
            build_id))

    @respond_to("^who is subscribed to (?P<build_id>\S+)")
    def check_subscribe(self, message, build_id):
        """
        who is subscribed to [buildid]: see the notification list for a token
        """
        notification_list = self.load("notify_" + build_id, {})
        self.reply(message, "Subscription list:")
        for room in notification_list:
            self.reply(message, "{}:  {}".format(room, ', '.join(notification_list[room].replace("@", "").strip().split(" "))))



