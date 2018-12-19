from __future__ import print_function
import sys
import signal
import os
import readline
import shlex
import json
from terminal_colors import Color
import requests
import getpass
import inspect
import subprocess
import re
from distutils.version import LooseVersion

class CLIReactor(object):
    """Hermes Command Line Interface."""
    colors = {
        'regular' : Color(),
        'prompt'  : Color().bright_blue,
        'header'  : Color().bold.white,
        'warning' : Color().red,
        'task'    : Color().cyan,
        'epic'    : Color().magenta,
        'ok'      : Color().green,
        'tip'     : Color().yellow,
    }

    def __init__(self):
        """Initialization."""
        self.event_loop_active = True
        self.headers = {'Content-Type': 'application/json',}
        self.user = None
        self.auth = (self.user, None)

    def run(self):
        """Run script."""
        self.help()

        while self.event_loop_active:
            prompt_color = self.colors['prompt'].readline_escape
            prompt = "{} >> ".format(prompt_color('Hermes'))

            try:
                self.dataReceived(input(prompt))
            except EOFError:
                self.write("\n")
                return
            except KeyboardInterrupt:
                self.write("\n")
                pass

    def write(self, string, color="regular"):
        """Print method with custom colors.

        :param string: String to print
        :param color: Font color, default is 'regular'
        """
        sys.stdout.write(self.colors[color](string))

    def comment(self, ticket, comment):
        """Comment on a Jira ticket.

        param ticket: Jira ticket key.
        param comment: Comment to post to ticket.
        """
        self.jiraLogin()

        url = 'https://jira.esss.lu.se/rest/api/latest/issue/'+ticket+'/comment'
        payload = '{"body":"'+comment+'"}'
        response = requests.post(
            url, auth=self.auth, headers=self.headers, data=payload)
        self.response_ok(response, ticket)

    def comments(self, ticket):
        """Get comments on a Jira ticket.

        param ticket: Jira ticket key.
        """
        self.jiraLogin()

        url = 'https://jira.esss.lu.se/rest/api/latest/issue/'+ticket+'/comment'
        response = requests.get(url, auth=self.auth, headers=self.headers)

        if not self.response_ok(response, ticket):
            return

        data = response.json()
        raw_com = data['comments']

        names = []
        comments = []
        for i in range(len(raw_com)):
            names.append(raw_com[i]['author']['displayName'])
            comment = raw_com[i]['body']
            comments.append(comment.replace('\n', '').replace('\r', ''))

        rows, cols = os.popen('stty size', 'r').read().split()
        max_len = len(max(names, key=len)) + 1
        for i in range(len(names)):
            if i % 2 == 0:
                color = 'task'
            else:
                color = 'epic'
            spacing = " "*(max_len - len(names[i]))
            self.write('{}:{}' .format(names[i], spacing), color)

            line_len = max_len + 1
            col_nbr = 0
            for k in range(len(comments[i])):
                col_nbr += 1
                if line_len + col_nbr == int(cols):
                    col_nbr = 0
                    self.write('\n{}' .format(" "*(max_len+1)))
                    if comments[i][k] == ' ':
                        continue

                self.write('{}' .format(comments[i][k]), color)
            print('')

    def log(self, ticket, time, comment):
        """Log work.

        param ticket: Jira ticket key.
        param time: Time spent to post to ticket's work log..
        param comment: Comment to post to ticket's work log.
        """
        self.jiraLogin()

        url = 'https://jira.esss.lu.se/rest/api/2/issue/'+ticket+'/worklog'
        payload = '{"timeSpent":"'+time+'","comment":"'+comment+'"}'
        response = requests.post(
            url, auth=self.auth, headers=self.headers, data=payload)
        self.response_ok(response, ticket)

    def response_ok(self, response, ticket=None):
        """Parse Jira response message

        :param response: Jira response message
        :param ticket: Jira ticket

        :returns: True if request was successful, False otherwise
        """
        data = response.json()
        curframe = inspect.currentframe()
        calframe = inspect.getouterframes(curframe, 2)
        caller = calframe[1][3]

        if response.status_code == 200:
            return True # Successful 'get'
        elif response.status_code == 201:
            self.write('Successfully posted\n', 'ok')
            return True
        elif response.status_code == 400:
            if data['errorMessages']:
                errorMessages = data['errorMessages'][0]
                self.write('{}\n' .format(errorMessages), 'warning')

            if caller == 'assign':
                errors = data['errors']['assignee']
                self.write('{}\n' .format(errors), 'warning')
            elif caller == 'log':
                errors = data['errors']['timeLogged']
                self.write('{}\n' .format(errors), 'warning')

        elif response.status_code == 404:
            errorMessages = data['errorMessages'][0]
            self.write('{}\n' .format(errorMessages), 'warning')

        return False

    def tickets(self, key=None, target='assignee'):
        """Lists all tickets for assigned to a user or project.

        :param key: Name of Jira user or project
        :param target: 'assignee' or 'project', default is 'assignee'
        """
        self.jiraLogin()

        if key is None and target == 'assignee':
            key = self.user

        url = 'https://jira.esss.lu.se/rest/api/2/search?jql='+target+'=' + key
        response = requests.get(url, auth=self.auth, headers=self.headers)
        response_ok = self.response_ok(response)

        if response_ok is False:
            return

        data = response.json()
        issues = data['issues']

        if not issues:
            self.write('No tickets found for \'{}\' \n' .format(key),'warning')
            return

        n = len(issues)
        tickets = []
        issue_types = []
        progress = []
        parent_keys = []
        summaries = []
        tree = []
        orphanage = ['Orphans', 'None', 'None', 'These children have no epic']
        has_orphanage = False

        # Find all parents
        for i in range(0, n):
            tickets.append(issues[i]['key'])
            issue_types.append(issues[i]['fields']['issuetype']['name'])
            s = issues[i]['fields']['summary']
            summaries.append(s[0:34]+'...' if len(s)>37 else s)

            try:
                p = str(issues[i]['fields']['aggregateprogress']['percent'])+'%'
            except Exception:
                p = 'None'

            progress.append(p)

            if issue_types[i] == "Epic":
                parent = [tickets[i], issue_types[i], progress[i], summaries[i]]
                parent_keys.append(tickets[i])
                tree.append((parent,[]))

        for i in range(0, n):
            if issue_types[i] == 'Sub-task':
                parent_key = issues[i]['fields']['parent']['key']
                if parent_key not in parent_keys:
                    parent = [parent_key, 'Unknown', 'Unknown', 'Unknown']
                    parent_keys.append(parent_key)
                    tree.append((parent,[]))
            else:
                parent_key = issues[i]['fields']['customfield_10008']
                if not has_orphanage:
                    has_orphanage = True
                    tree.append((orphanage,[]))


        # Set all children
        for i in range(0, n):
            parent_key = issues[i]['fields']['customfield_10008']
            if parent_key is None and issue_types[i] == 'Sub-task':
                parent_key = issues[i]['fields']['parent']['key']
            elif parent_key is None and issue_types[i] != 'Epic':
                parent_key = 'Orphans'

            for parent, children in tree:
                if parent[0] == parent_key:
                    children.append(
                        [tickets[i], issue_types[i], progress[i], summaries[i]])

        # Print headers
        self.write('{:<18s}{:<15s}{:<10s}{}\n' .format('Ticket',
                                                  'Type',
                                                  'Progress',
                                                  'Summary'), 'header')
        # Print tree
        for parent, children in tree:
            self.write('{:<18s}{:<15s}{:<10s}{}\n' .format(parent[0],
                                                        parent[1],
                                                        parent[2],
                                                        parent[3]), 'epic')

            for child in children:
                self.write('   {:<15s}{:<15s}{:<10s}{}\n' .format(child[0],
                                                            child[1],
                                                            child[2],
                                                            child[3]), 'task')

    def quit(self):
        """Quit CLI."""
        self.event_loop_active = False
        os.kill(os.getpid(), signal.SIGINT)

    def install(self, tool, dest, opt=None):
        """Install software tool.

        param tool: Name of tool to install
        param args: Input arguments needed for installation
        """
        sudo = False
        path = os.path.dirname(os.path.abspath(__file__)) # Path to cli dir
        dest = os.path.expanduser(dest)

        # Check privileges
        priv_dest = dest
        while not os.path.exists(priv_dest):
            priv_dest = os.path.split(priv_dest)[0]
        if not os.access(priv_dest, os.W_OK):
            self.write('You need sudo privileges to write in {}\n'
                           .format(priv_dest))

            q_sudo = input('Would you like to run as sudo? [Y/n]: ').lower()
            if q_sudo == "y":
                sudo = True
            else:
                return

        # Check if destination directory exists
        if not os.path.exists(dest):
            create = input('Directory does not exist, '
                           +'would you like to create it? [Y/n]: ').lower()
            if create == "y":
                os.makedirs(dest)
            else:
                return

        if tool == 'e3':
            # Check if already installed
            if os.path.exists(dest+'/e3'):
                overwrite = input('E3 found in the destination directory. '
                                   +'Overwrite existing E3? [Y/n]: ').lower()
                if not overwrite == "y":
                    return

            try:
                self.write('Installing {}\n' .format(tool), 'task')
                ret_code = subprocess.check_call('sudo {}/e3.install {}'
                                                     .format(path, dest),
                                                     shell=True)
            except subprocess.CalledProcessError as e:
                self.write(e, 'warning')
                return

        elif tool == 'plcfactory':
            # Check if already installed
            existing = False
            if os.path.exists(dest+'/ics_plc_factory'):
                overwrite = input('PLCFactory found in the destination directory. '
                                   +'Update PLCFactory? [Y/n]: ').lower()
                if overwrite == "y":
                    existing = True
                else:
                    return

            repo_url = 'https://bitbucket.org/europeanspallationsource/ics_plc_factory.git'

            if existing:
                cmd = 'git -C ' + dest + '/ics_plc_factory pull ' + repo_url
            else:
                cmd = 'git clone ' + repo_url + ' ' + dest + '/ics_plc_factory'

            if sudo:
                cmd = 'sudo ' + cmd

            try:
                ret_code = subprocess.check_call(cmd, shell=True)
            except Exception as e:
                ret_code = 1

        elif tool == 'beast':
            # Check if already installed
            existing = False
            if os.path.exists(dest+'/beast-config'):
                overwrite = input('BEAST found in the destination directory. '
                                   +'Update BEAST? [Y/n]: ').lower()
                if overwrite == "y":
                    existing = True
                else:
                    return

            repo_url = 'https://gitlab.esss.lu.se/ics-infrastructure/beast-config.git'

            if existing:
                cmd = 'git -C ' + dest + '/beast-config pull ' + repo_url
            else:
                cmd = 'git clone ' + repo_url + ' ' + dest + '/beast-config'

            if sudo:
                cmd = 'sudo ' + cmd

            try:
                ret_code = subprocess.check_call(cmd, shell=True)
            except Exception as e:
                ret_code = 1

        elif tool == 'css':
            if opt is None:
                opt = 'development'
            elif opt != "development" and opt != "production":
                self.write('\'{}\' is an invalid option\n' .format(opt), 'warning' )
                return

            # Check if already installed
            if os.path.exists(dest+'/cs-studio'):
                overwrite = input('CSS found in the destination directory. '
                                   +'Overwrite existing CSS? [Y/n]: ').lower()

            url = 'https://artifactory.esss.lu.se/artifactory/CS-Studio/'
            url += opt+'/'
            if opt == 'development':
                pattern = re.compile("[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+b[0-9]+")
            elif opt == 'production':
                pattern = re.compile("[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+")

            params = {'q': 'ISOW7841FDWER'}
            headers = {'User-Agent': 'Mozilla/5'}
            r = requests.get(url, params=params, headers=headers)

            # Cast to set and back to list to make it unique
            versions = set(pattern.findall(r.text))
            versions = list(versions)


            versions.sort(key=LooseVersion)
            self.write('{} versions:\n' .format(opt), 'header')
            for v in versions:
                print(v)
            version = input('Which version would you like to install? ')
            while version not in pattern.findall(r.text):
                self.write('\'{}\' is not a valid version\n'
                               .format(version), 'warning')
                version = input('Which version would you like to install? ')

            self.write('Downloading {} version {}\n'
                           .format(opt, version), 'task')

            if sys.platform == 'linux':
                file_name = 'cs-studio-ess-'+version+'-linux.gtk.x86_64.tar.gz'

            url += version+'/'+file_name

            with open(path+'/'+file_name, 'wb') as f:
                response = requests.get(url, stream=True)
                total_length = int(response.headers.get('content-length'))
                dl = 0
                for data in response.iter_content(chunk_size=4096):
                    dl += len(data)
                    f.write(data)
                    progress = int(100 * dl / total_length)
                    self.write('\r{}%' .format(progress), 'task')
                    sys.stdout.flush()

            self.write("\nInstalling\n", 'task')


            cmd = 'sudo tar xzf ' + path+'/'+file_name + ' -C ' + dest
            cmd += ' && rm -rf ' + path+'/'+file_name


            if sudo:
                cmd = 'sudo ' + cmd

            ret_code = subprocess.check_call(cmd, shell=True)

            self.write("Done\n", 'task')

            self.write('Tip: If you want to be able to start CSS from '
                           +'anywhere in your terminal by just\ntyping "css"'
                           +', put the following line in your ~/.bashrc file:\n'
                           +'alias css=\'{}/cs-studio/ESS\ CS-Studio\'\n'
                           .format(dest), 'tip')

        else:
            self.write('\'{}\' is not available for installation\n'
                           .format(tool), 'warning')
            return

        if ret_code != 0:
            self.write('\'{}\' installation failed\n' .format(tool), 'warning')
            return

    def username(self, action):
        """Settings for logged in user.

        Username can be stored in local file, or removed from
        it. Remembering the username will enable logging into Jira using
        password only.

        param action: 'remember' or 'forget' username
        """
        path = os.path.dirname(os.path.abspath(__file__))
        user_file = 'jira_cli.user'

        if action == 'remember':
            if os.path.isfile(path+'/'+user_file):
                self.write('{} is already remembered\n'
                               .format(self.user), 'warning')
            else:
                with open(path+'/'+user_file, 'a') as f:
                    f.write('{"user":"'+user+'"}')
                    self.write('{} will be remembered\n' .format(self.user), 'ok')
        elif action == 'forget':
            if os.path.isfile(path+'/'+user_file):
                os.remove(path+'/'+user_file)
                self.write('{} has been forgotten\n' .format(self.user), 'ok')
            else:
                self.write('{} was not known\n' .format(self.user), 'warning')
        else:
            self.write('Invalid action \'{}\'\n' .format(action), 'warning')

    def help(self):
        """Print help text."""
        assign_descr = 'Assign an issue to a user'
        help_descr = 'List commands (show this message)'
        comment_descr = 'Comment on a tickets e.g. "comment"'
        comments_descr = 'Get all comments on a ticket'
        log_descr = 'Log work, e.g. log "3h 20m" "comment"'
        org_descr = 'Parse emacs org-mode file and log work'
        quit_descr = 'Quit Hermes'
        tickets_a_descr = 'List assignee\'s tickets'
        tickets_p_descr = 'List project\'s tickets'
        username_descr = 'Remember or forget username'
        install_e3 = 'Install e3 with epics 7 + common mods'
        install_css = 'Install css production|development'
        install_plcf = 'Install plc factory'
        install_beast = 'Install BEAST alarm handler'

        help_text = {
            # name                                      function
            'Hermes'                                  : None,
            'help'                                    : help_descr,
            'quit'                                    : quit_descr,
            'Jira'                                    : None,
            'assign    <ticket> <assignee>'           : assign_descr,
            'comment   <ticket> "<comment>"'          : comment_descr,
            'comments  <ticket>'                      : comments_descr,
            'log       <ticket> "<time>" "<comment>"' : log_descr,
            'org       <path>'                        : org_descr,
            'tickets   [<assignee>]'                  : tickets_a_descr,
            '          [<project> project]'           : tickets_p_descr,
            'username  remember | forget'             : username_descr,
            'Installation'                            : None,
            'install   e3 <install path>'             : install_e3,
            '          css <install path> [<branch>]' : install_css,
            '          plcfactory <install path>'     : install_plcf,
            '          beast <install path>'          : install_beast,
            }

        title = "Commands:"
        # Find longest command in order to make list as compact as possible
        cols = max(len(max(help_text.keys(), key=lambda x: len(x))), len(title))

        self.write('{} {} Description:'
                       .format(title, " "*(cols - len(title))), "header")

        commands = help_text.keys()

        for cmd in commands:
            spacing = " "*(cols - len(cmd))
            if help_text[cmd] is None:
                self.write('\n({})\n' .format(cmd), 'task')
            else:
                self.write("%s %s %s\n"
                           %(cmd, spacing, help_text[cmd]))

    def assign(self, ticket, user):
        """Assign an issue to a user.

        param ticket: Jira issue.
        param user: The issue assigne to be set.
        """
        self.jiraLogin()

        url = 'https://jira.esss.lu.se/rest/api/2/issue/'+ticket
        payload = '{"fields":{"assignee":{"name":"'+user+'"}}}'
        response = requests.put(
            url, auth=self.auth, headers=self.headers, data=payload)

        # Jira seems to return a bad json formatted string and the package
        # 'requests' throws an exception. Until that is fixed, this will be
        # caught and handled by the exception below.
        try:
            self.response_ok(response, ticket)
        except json.decoder.JSONDecodeError as e:
            s = str(response).split(']>')
            s = s[0].split('[')
            response_code = int(s[1])

            if response_code == 204:
                self.write('{} assigned to {}\n'
                               .format(ticket, user), 'ok')

    def dataReceived(self, data):
        """Handles request from the command line.

        param data: Input data from command line.
        """
        # Split command from argument and strip from whitespace etc.
        data = data.strip()
        command, _, data = data.partition(' ')
        data = data.strip()

        # Check if the request is empty
        if not command:
            return

        commands = {
            # name           function
            # HERMES ######################
            "help"          : self.help,
            "quit"          : self.quit,
            # JIRA ########################
            "assign"        : self.assign,
            "comment"       : self.comment,
            "comments"      : self.comments,
            "log"           : self.log,
            "org"           : self.org,
            "tickets"       : self.tickets,
            "username"      : self.username,
            # INSTALL #####################
            "install"       : self.install,
            }

        # Check if we have a valid command
        if command not in commands:
            self.write("Invalid command '{}'\n" .format(command), "warning")
            self.write("Type 'help' to see all commands\n")
            return

        function = commands[command]

        try:
            args = self.parse(data)
            function(*args)
        except TypeError as type_error:
            self.write("{}\n".format(type_error), "warning")

    def org(self, path):
        """ parse emacs org-mode file with clock table and send work to Jira.

        :param path: Path to .org file
        """
        path = os.path.expanduser(path)

        if not os.path.exists(path):
            self.write('File \'{}\' does not exist\n' .format(path), 'warning')
            return

        with open(path) as f:
            lines = f.readlines()

        match_flag = False

        tickets = []
        times = []
        comments = []
        for i in range(0,len(lines)):
            match = re.search("^\|[^-][^ Headline][^ \*Total].*ICSHWI", lines[i])
            if match is not None:
                match_flag = True
                cols = lines[i].split('|')
                tickets.append(re.search("ICSHWI(-\d+)?", cols[1]).group(0))
                comments.append(cols[5])
                time_list = re.search("\d+:\d+", lines[i]).group(0).split(':')
                times.append('{}h {}m' .format(time_list[0], time_list[1]))
                self.write('{}\t{}\t{}\n' .format(tickets[-1], times[-1], comments[-1]))

        if match_flag:
            log = input('Would you like to log this? [Y/n]: ').lower()
            if log == "y":
                self.jiraLogin()
                for i in range(len(tickets)):
                    self.write('\n{}\t{}\t{}\n' .format(tickets[i], times[i], comments[i]))
                    self.log(tickets[i], times[i], comments[i])
            else:
                return
        else:
            self.write('No work to log found in {}\n' .format(path), 'warning')

    def parse(self, args, comments=False, posix=True):
        """Parse command from command line.

        :param args: Command line arguments
        :param comments: shlex parameter
        :param posix: shlex parameter

        :returns: Split arguments
        """
        slit_args = shlex.split(args, comments, posix)
        return slit_args

    def jiraLogin(self):
        """" Login to Jira account. """
        user_file = 'jira.user'
        script_file = os.path.basename(__file__)
        path = os.path.dirname(os.path.abspath(__file__))

        if self.user is not None:
            return

        if os.path.isfile(path+'/'+user_file):
            with open(path+'/'+user_file, 'r') as f:
                json_data = json.load(f)
                self.user = json_data['user']
        else:
            self.user = input("Jira username: ")

        password = getpass.getpass("Password: ")
        self.auth = (self.user, password)

        #TODO: verify self.auth
        # login_ok = False
        # while not login_ok:
        #     password = getpass.getpass("Password: ")
        #     self.auth = (self.user, password)
        #     try:
        #         url = 'https://jira.esss.lu.se/rest/api/2/search?jql=assignee=' + self.user
        #         response = requests.get(url, auth=self.auth, headers=self.headers)
        #         # response_ok = self.response_ok(response)
        #         login_ok = True
        #     except Exception:
        #         self.write('try again\n', 'warning')



if __name__ == '__main__':
    reactor = CLIReactor()
    reactor.run()
