from will.plugin import WillPlugin
from will.decorators import respond_to
from will import settings
import pyotp
import qrcode
import boto
import boto.s3.connection
from boto.s3.key import Key
import pprint
import cStringIO

def check_user_permission(storage, username, permission):
    """
    Function to check User permissions.  Requires an instance of WillPlugin to be passed in as 'storage'
    """
    user_permissions = storage.load("user_permissions", {})
    if not user_permissions:
        if hasattr(settings, "ADMIN_USERS"):
            for admin_user in settings.ADMIN_USERS.split(','):
                user_permissions[admin_user] = ['admin', 'grant']
                storage.say("giving {} admin permissions".format(admin_user))
            storage.save("user_permissions", user_permissions)
        else:
            msg = "No admin users are defined in redis or environment"
            storage.say(msg)
    if username in user_permissions:
        if permission in user_permissions[username]:
            return 1
        else:
            return 0
    else:
        return 0

def verify_twofactor(storage, username, token):
    """
    Function to check User's twofactor token.  Requires an instance of WillPlugin to be passed in as 'storage'
    """
    user_twofactor = storage.load("user_twofactor", {})
    if username in user_twofactor:
        totp = pyotp.TOTP(user_twofactor[username])
        return totp.verify(token)
    else:
        return 0

class UserPlugin(WillPlugin):
    def __init__(self):
        if not hasattr(settings, "TWOFACTOR_ISSUER"):
            msg = "Error: TWOFACTOR_ISSUER not defined in the environment"
            self.say(msg)
        if not hasattr(settings, "TWOFACTOR_PRINCIPLE"):
            msg = "Error: TWOFACTOR_PRINCIPLE not defined in the environment"
            self.say(msg)
        if not hasattr(settings, "TWOFACTOR_S3_BUCKET"):
            msg = "Error: TWOFACTOR_S3_BUCKET not defined in the environment"
            self.say(msg)
        if not hasattr(settings, "TWOFACTOR_S3_PROFILE"):
            msg = "Error: TWOFACTOR_S3_PROFILE not defined in the environment"
            self._say_error(msg)


            


    def generate_and_upload_QR(self, secret, username):
        try:
            principle = settings.TWOFACTOR_PRINCIPLE
            issuer = settings.TWOFACTOR_ISSUER
            s3_twofactor_bucket = settings.TWOFACTOR_S3_BUCKET
            s3_twofactor_profile = settings.TWOFACTOR_S3_PROFILE
        except:
            return "Settings for S3 hosted QR codes not configured properly in environment"

        url = 'otpauth://totp/{issuer}:{principle}?secret={secret}&issuer={issuer}'.format(issuer=issuer, principle=principle, secret=secret)
        img = qrcode.make(url)
        conn = boto.connect_s3(profile_name=s3_twofactor_profile)
        bucket = conn.get_bucket(s3_twofactor_bucket)
        key = Key(bucket)
        key.key = '{}-qr.png'.format(username)
        image_buffer = cStringIO.StringIO()
        img.save(image_buffer, 'PNG')
        key.set_contents_from_string(image_buffer.getvalue())
        url = key.generate_url(60,query_auth=True, force_http=True)
        return url

    @respond_to("^twofactor verify (?P<token>\w+)")
    def verify_user_twofactor(self, message, token):
        """
        twofactor verify [token]: verify your twofactor authentication system
        """
        username = message.sender.nick
        if verify_twofactor(self,username, token):
            self.reply(message, "You are authenticated, {}".format(username)) 
        else:
            self.reply(message, "That's not correct, {}".format(username))


    @respond_to("^twofactor me")
    def create_user_twofactor(self, message):
        """
        twofactor me: set up twofactor authentication for your user
        """
        username = message.sender.nick
        user_twofactor = self.load("user_twofactor", {})
        if username not in user_twofactor:
            user_twofactor[username] = pyotp.random_base32()
            message['type'] = 'chat'
            self.reply(message, self.generate_and_upload_QR(user_twofactor[username], username))
            self.save("user_twofactor", user_twofactor)
            self.reply(message, "say 'twofactor verify <token>' to check your twofactor verification")
        else:
            message['type'] = 'chat'
            self.reply(message, "twofactor is already set up!") 

    @respond_to("^twofactor remove (?P<username>\w+)")
    def remove_user_twofactor(self, message, username):
        """
        twofactor remove [username]: remove [username]'s twofactor authentication (requires 'admin' permission)
        """
        if check_user_permission(self, message.sender.nick, 'admin'):  
            user_twofactor = self.load("user_twofactor", {})
            if username in user_twofactor:
                del user_twofactor[username]
                self.reply(message, "twofactor is removed for {}".format(username)) 
                self.save("user_twofactor", user_twofactor)
            else:
                self.reply(message, "twofactor was not set for {}".format(username)) 
        else:
            self.say("@{}: you don't have admin permission".format(message.sender.nick), message=message) 

    @respond_to("^what can (?P<username>\w+) do")
    def show_user_permission(self, message, username):
        """
        what can [username] do?: get someone's permissions
        """
        if 'i' == username.lower():
            username = message.sender.nick
        user_permissions = self.load("user_permissions", {})
        try:
            self.say("@{}: {}'s permissions are: {}".format(message.sender.nick, username, ', '.join(user_permissions[username])), message=message) 
        except KeyError:
            self.say("@{}: I don't know who {} is. (no permissions)".format(message.sender.nick, username), message=message)


    @respond_to("^who can(?P<permissions>( \w+)+)")
    def find_user_permission(self, message, permissions):
        """
        who can [permission]?: find the list of people with a permission
        """
        user_permissions = self.load("user_permissions", {})
        userlist = [user for user, user_perm in user_permissions.items() 
                for permission in permissions.split() 
                    if permission in user_perm]
        self.say("@{}: {} can {}".format(message.sender.nick, ', '.join(userlist), permission), message=message)

    @respond_to("^can I(?P<permissions>( \w+)+)")
    def confirm_user_permission(self, message, permissions):
        """
        can I [permission]?: check if you have a specific permission
        """
        for permission in permissions.split():
            if check_user_permission(self, message.sender.nick, permission):  
                self.say("@{}: Yes, you can {}".format(message.sender.nick, permission), message=message)
            else:
                self.say("@{}: No, you can't {}".format(message.sender.nick, permission), message=message)



    @respond_to("^give (?P<username>\w+) permission(?P<permissions>( \w+)+)")
    def give_user_permission(self, message, username, permissions):
        """
        give [username] permission to [permission]: grant a user a permission (requires 'grant' permission)
        """
        if check_user_permission(self, message.sender.nick, 'grant'):  
            requested_permissions = permissions.split()
            try:
                requested_permissions.remove("to")
            except ValueError:
                pass
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


    @respond_to("^take away from (?P<username>\w+) permission(?P<permissions>( \w+)+)")
    def remove_user_permission(self, message, username, permissions):
        """
        take away from [username] permission [permission]: remove someone's permission (requires grant permission)
        """
        if check_user_permission(self, message.sender.nick, 'grant'):  
            requested_permissions = permissions.split()
            try:
                requested_permissions.remove("to")
            except ValueError:
                pass
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
        else:
            self.say("@{}: you don't have grant permission".format(message.sender.nick), message=message) 

