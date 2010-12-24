"""
Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f <file_name>.py [-i <private_key>] -H <server> <deployment_conf> install
"""
# for Python 2.5
from __future__ import with_statement

from fabric.api import sudo, run, cd, env
from fabric.contrib.files import exists, settings, hide
from contextlib import nested

from shogun_fab import Shogun as shogun
from weblogo_fab import Weblogo as weblogo
from util import base_conf
from util import util
from util import common
import os

class Kirmes(base_conf.Deployment):
    
    def resolve_dependencies(self, install=True):
        """ Install any tool dependencies. If the optional 'install' parameter
        is False, only check if the dependencies are installed but do not try to 
        install them. """
        print "Resolving dependencies for %s..." % str(self.tool_env['pkg_name']).upper()
        ret_env = {}
        parent_env = {'parent': self.tool_env}
        d = shogun(self.conf)
        if d.is_installed(parent_env):
            ret_env = d.get_env(parent_env)
        elif install:
            ret_env = d.install(parent_env)
        else:
            # Not installed, not installing, but checking if it were installed
            return False
        if ret_env.has_key('env_script'):
            self.tool_env['shogun_env_script'] = ret_env['env_script']
        else:
            print "----- ERROR: Could not install Shogun -----"
            return False
        parent_env = {'parent': self.tool_env} # Reset parent_env
        d = weblogo(self.conf)
        if d.is_installed(parent_env):
            ret_env = d.get_env(parent_env)
        elif install:
            ret_env = d.install(parent_env)
        else:
            # Not installed, not installing, but checking if it were installed
            return False
        if not ret_env['installed']:
            return False 
        self.tool_env['dependencies_ok'] = True
        return True
    
    def set_env(self, local_env={}):
        """ Setup the values for the global environment for the current tool. """
        self.tool_env['pkg_name'] = 'kirmes'
        print "Setting up environment for %s" % str(self.tool_env['pkg_name']).upper()
        if local_env != {}:
            self.tool_env = common.merge_dicts(self.tool_env, local_env)
        env.user = self.tool_env['user']
        self.tool_env = common.check_for_parent(self.tool_env, local_env)
        if not common.check_install_dir_root(self.tool_env):
            return False
        if not common.check_galaxy(self.tool_env):
            return False
        if self.tool_env['version'] == '':
            self.tool_env['version'] = "0.8"
        if not self.tool_env.has_key('url'):
            self.tool_env['url'] = "http://www.fml.tuebingen.mpg.de/raetsch/suppl/kirmes/kirmes-%s.tar.gz" % self.tool_env['version']
        self.tool_env['install_dir'] = os.path.join(self.tool_env['install_dir_root'], self.tool_env['pkg_name'], self.tool_env['version'])
        self.tool_env['env_set'] = True
        return True
    
    def is_installed(self, local_env={}):
        """ Check if the current tool and its dependencies are installed. Optionally, 
        missing dependencies may be installed."""
        if not self.tool_env['env_set']: 
            self.set_env(local_env)
        print "Checking if %s %s is installed..." % (self.tool_env['pkg_name'], self.tool_env['version'])
        if not self.tool_env['dependencies_ok']:
            if not self.resolve_dependencies(install=False):
                return False        
        install_cmd = sudo if self.tool_env['use_sudo'] else run
        with nested(cd(self.tool_env['install_dir']), settings(hide('stdout'), warn_only=True)):
            result = install_cmd("source env.sh; python kirmes.py --help")
            if result.return_code == 0:
                self.tool_env['installed'] = True
                print "%s %s is installed in %s" % (str(self.tool_env['pkg_name']).upper(), self.tool_env['version'], self.tool_env['install_dir'])
                return True
        print "%s %s is not installed." % (self.tool_env['pkg_name'], self.tool_env['version'])
        return False
    
    def install(self, local_env={}, force=False):
        """ If not already installed, install given tool and all of its dependencies. """
        if self.set_env(local_env):
            if (not self.is_installed(local_env=local_env) and not self.tool_env['fatal_error']) or force:
                if not self.tool_env['dependencies_ok']:
                    if not self.resolve_dependencies():
                        print "----- ERROR resolving dependencies -----"
                        return False
                # Maybe the dependencies is all that was missing so check if 
                # the tool can be considered as installed now
                if not self.is_installed(local_env=local_env) or force:
                    print "Trying to install %s %s as user %s" % (self.tool_env['pkg_name'], self.tool_env['version'], env.user)
                    install_cmd = sudo if self.tool_env['use_sudo'] else run
                    if not exists(self.tool_env['install_dir']):
                        install_cmd("mkdir -p %s" % self.tool_env['install_dir'])
                    install_cmd("chown %s %s" % (env.user, self.tool_env['install_dir']))
                    with nested(cd(self.tool_env['install_dir']), settings(hide('stdout'))):
                        install_cmd("wget %s" % self.tool_env['url'])
                        install_cmd("tar xvzf %s" % os.path.split(self.tool_env['url'])[1])
                        install_cmd('rm %s' % os.path.split(self.tool_env['url'])[1])
                        if not exists('%s/tools/kirmes' % self.tool_env['galaxy_dir']):
                            install_cmd('mkdir %s/tools/kirmes' % self.tool_env['galaxy_dir'])
                        with settings(warn_only=True):
                            install_cmd('cp -n *.xml %s/tools/kirmes' % self.tool_env['galaxy_dir'])
                            install_cmd('chown -R %s %s/tools/kirmes' % (env.user, self.tool_env['galaxy_dir']))
                    if self.tool_env.has_key('shogun_env_script') and exists(self.tool_env['shogun_env_script']):
                        install_cmd("echo '. %s' > %s/env.sh" % (self.tool_env['shogun_env_script'], self.tool_env['install_dir']))
                        install_cmd("echo 'export PATH=%s:$PATH' >> %s/env.sh" % (self.tool_env['install_dir'], self.tool_env['install_dir']))
                        install_cmd("echo 'export PYTHONPATH=%s:$PYTHONPATH' >> %s/env.sh" % (self.tool_env['install_dir'], self.tool_env['install_dir']))
                        install_cmd("chmod +x %s/env.sh" % self.tool_env['install_dir'])
                        install_dir_root = os.path.join(self.tool_env['install_dir_root'], self.tool_env['pkg_name'])
                        install_cmd('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, self.tool_env['install_dir'], install_dir_root))
                        install_cmd('chown -R %s %s' % (env.user, install_dir_root))
                    else:
                        print "ERROR: Required dependency file for not found (for Shogun)."
                        self.tool_env['fatal_error'] = True
            # Make sure the tool installed correctly
            if not self.tool_env['fatal_error'] and (self.tool_env['installed'] or self.is_installed()):
                return common.compose_successful_return(self.tool_env)
        print "----- Problem installing KIRMES -----"
        return self.tool_env
    

instance = Kirmes()
util.add_class_methods_as_module_level_functions_for_fabric(instance, __name__)
