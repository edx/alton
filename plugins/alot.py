import datetime
import random
import requests
from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template


class AlotPlugin(WillPlugin):

    @hear("alot")
    def alot(self, message):
        """show off a picture of an alot when someone is talking about one """
        data = {"q": "alot", "v": "1.0", "safe": "active", "rsz": "8"}
        r = requests.get("http://ajax.googleapis.com/ajax/services/search/images", params=data)
        results = r.json()["responseData"]["results"]
        if len(results) > 0:
            url = random.choice(results)["unescapedUrl"]
            self.say("%s" % url, message=message)

