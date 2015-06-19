import datetime
from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template


class HelloPlugin(WillPlugin):

    @respond_to("^hello")
    def hello(self, message):
        self.reply(message, "hello everyone in {}!".format(self.get_room_from_message(message)['name']))

    @respond_to("^tell (?P<channel>\w+) (?P<what>.*)")
    def tell(self, message, channel, what):
        self.reply(message, "OK!")
        self.say(what, room=self.get_room_from_name_or_id(channel))

