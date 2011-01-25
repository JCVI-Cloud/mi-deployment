"""
Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f <file_name>.py [-i <private_key>] -H <server> <deployment_conf> install
"""
# for Python 2.5
from __future__ import with_statement

from fabric.api import env

from util import base_conf
from util import util
from util import common

class Weblogo(base_conf.Deployment):

    def set_env(self, local_env={}):
        self.tool_env['pkg_name'] = "weblogo"
        print "Setting up environment for %s" % str(self.tool_env['pkg_name']).upper()
        if local_env != {}:
            self.tool_env = common.merge_dicts(self.tool_env, local_env)
        env.user = self.tool_env['user']
        self.tool_env = common.check_for_parent(self.tool_env, local_env)
        self.tool_env['version'] = "3"
        self.tool_env['env_set'] = True
        return True
    
    def is_installed(self, local_env={}):
        if not self.tool_env['env_set']: 
            self.set_env(local_env)
        print "Checking if Weblogo %s is installed..." % self.tool_env['version']
        cmd = 'python -c"import weblogolib"'
        if common.cmd_success(self.tool_env, cmd):
            self.tool_env['installed'] = True
            print "%s %s is installed system wide." % (str(self.tool_env['pkg_name']).upper(), self.tool_env['version'])
            return True
        print "Weblogo %s is not installed." % self.tool_env['version']
        return False
    
    def install(self, local_env={}):
        if self.set_env(local_env):
            if not self.is_installed():
                print "Trying to install Weblogo %s as user %s" % (self.tool_env['version'], env.user)
                common.install_required_packages(['ghostscript'])
                libraries = ['numpy', 'corebio', 'weblogo']
                common.install_required_python_libraries(self.tool_env, libraries)
            if self.tool_env['installed'] or self.is_installed():
                return common.compose_successful_return(self.tool_env)
        print "----- Problem installing EasySVM -----"
        return self.tool_env
    

instance = Weblogo()
util.add_class_methods_as_module_level_functions_for_fabric(instance, __name__)
