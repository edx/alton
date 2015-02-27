from will.plugin import WillPlugin
from will.decorators import respond_to
import pyotp
import qrcode
import boto
import boto.s3.connection
from boto.s3.key import Key
import pprint
import cStringIO

class UserPlugin(WillPlugin):
    def check_user_permission(self, username, permission):
        user_permissions = self.load("user_permissions", {})

        if username in user_permissions:
            if permission in user_permissions[username]:
                return 1
            else:
                return 0
        else:
            return 0

    def generate_and_upload_QR(self,secret,username):
        principle = 'devops@edx.org'
        issuer = 'edx-ops'
        url = 'otpauth://totp/{issuer}:{principle}?secret={secret}&issuer={issuer}'.format(issuer=issuer, principle=principle, secret=secret)
        img = qrcode.make(url)
        conn = boto.connect_s3(profile_name='edx')
        bucket = conn.get_bucket('edx-twofactor')
        key = Key(bucket)
        key.key = '{}-qr.png'.format(username)
        image_buffer = cStringIO.StringIO()
        img.save(image_buffer, 'PNG')
        key.set_contents_from_string(image_buffer.getvalue())
        url = key.generate_url(60,query_auth=True, force_http=True)
        return url

    @respond_to("^twofactor verify (?P<secret>\w*)")
    def verify_twofactor(self, message, secret):
        user_twofactor = self.load("user_twofactor", {})
        username = message.sender.nick
        if username in user_twofactor:
            totp = pyotp.TOTP(user_twofactor[username])
            if totp.verify(secret):
                self.reply(message, "You are authenticated, {}".format(username)) 
            else:
                self.reply(message, "That's not correct,{}".format(username))

        else:
            self.reply(message, "twofactor auth not set up for {}".format(username)) 


    @respond_to("^debug messageobject")
    def debug_messageobject(self, message):
        pp = pprint.PrettyPrinter(indent=4)
        message['type'] = 'chat'
        self.reply(message, pp.pformat(message))

    @respond_to("^twofactor me")
    def create_user_twofactor(self, message):
        username = message.sender.nick
        user_twofactor = self.load("user_twofactor", {})
        if username not in user_twofactor:
            user_twofactor[username] = pyotp.random_base32()
            message['type'] = 'chat'
            #self.reply(message, "twofactor is: {}".format(user_twofactor[username])) 
            self.reply(message, self.generate_and_upload_QR(user_twofactor[username], username))
            self.save("user_twofactor", user_twofactor)
        else:
            message['type'] = 'chat'
            self.reply(message, "twofactor is already set up!") 

    @respond_to("^twofactor remove (?P<username>\w*)")
    def remove_user_twofactor(self, message, username):
        user_twofactor = self.load("user_twofactor", {})
        if username in user_twofactor:
            del user_twofactor[username]
            self.reply(message, "twofactor is removed for {}".format(username)) 
            self.save("user_twofactor", user_twofactor)
        else:
            self.reply(message, "twofactor was not set for {}".format(username)) 


    @respond_to("^what can (?P<username>\w*) do")
    def show_user_permission(self, message, username):
        if 'I' == username:
            username = message.sender.nick
        user_permissions = self.load("user_permissions", {})
        try:
            self.say("@{}: {}'s permissions are: {}".format(message.sender.nick, username, ', '.join(user_permissions[username])), message=message) 
        except KeyError:
            self.say("@{}: I don't know who {} is. (no permissions)".format(message.sender.nick, username), message=message)


    @respond_to("^who can(?P<permissions>( \w*)*)")
    def find_user_permission(self, message, permissions):
        user_permissions = self.load("user_permissions", {})
        for permission in permissions.split():
            userlist = []
            for user in user_permissions:
                if permission in user_permissions[user]:
                    userlist.append(user)
                else:
                    pass
            self.say("@{}: {} can {}".format(message.sender.nick, ', '.join(userlist), permission), message=message)

    @respond_to("^can I(?P<permissions>( \w*)*)")
    def confirm_user_permission(self, message, permissions):
        user_permissions = self.load("user_permissions", {})
        for permission in permissions.split():
            try:
                if permission in user_permissions[message.sender.nick]:
                    self.say("@{}: Yes, you can {}".format(message.sender.nick, permission), message=message)
                else:
                    self.say("@{}: No, you can't {}".format(message.sender.nick, permission), message=message)
            except KeyError:
                self.say("@{}: No, you can't {}".format(message.sender.nick, permission), message=message)



    @respond_to("^give (?P<username>\w*) permission(?P<permissions>( \w*)*)")
    def give_user_permission(self, message, username, permissions):
        if self.check_user_permission(message.sender.nick, 'grant'):  
            requested_permissions = permissions.split()
            try:
                requested_permissions.remove("to")
            except ValueError:
                pass
            #self.say("adding permissions : {} ".format(', '.join(requested_permissions)), message=message) 
            try:
                user_permissions = self.load("user_permissions", {})
                new_permissions = list(set(requested_permissions + user_permissions[username]))
            except KeyError:
                new_permissions = requested_permissions
            user_permissions[username] = new_permissions
            self.save("user_permissions", user_permissions)
            self.say("@{}: new permissions for {} are: {} ".format(message.sender.nick, username, ', '.join(new_permissions)), message=message) 
        else:
            self.say("@{}: you don't have grant permission".format(message.sender.nick), message=message) 


    @respond_to("^take away from (?P<username>\w*) permission(?P<permissions>( \w*)*)")
    def remove_user_permission(self, message, username, permissions):
        requested_permissions = permissions.split()
        requested_permissions.remove("to")
        #self.say("removing permissions : {} ".format(', '.join(requested_permissions)), message=message) 
        try:
            user_permissions = self.load("user_permissions", {})
            for permission in requested_permissions:
                try:
                    user_permissions[username].remove(permission)
                except ValueError:
                    pass
        except KeyError:
            pass
        self.save("user_permissions", user_permissions)
        self.say("@{}: new permissions for {} are: {} ".format(message.sender.nick, username, ', '.join(user_permissions[username])), message=message) 

    @respond_to("^trash all permissions")
    def delete_all_permission(self, message):
        self.save("user_permissions", {})
