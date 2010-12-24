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
from util import base_conf
from util import util
from util import common
import os

class EasySVM(base_conf.Deployment):
    
    def install_arff(self):
        common.install_required_python_libraries(self.tool_env, ['antlr_python_runtime'])
        version = '1.0c'
        url = 'http://www.mit.edu/~sav/arff/dist/arff-%s.tar.gz' % version
        with common.make_tmp_dir(self.tool_env) as work_dir:
            with cd(work_dir):
                install_cmd = sudo if self.tool_env['use_sudo'] else run
                install_cmd('wget %s' % url)
                install_cmd("tar xvzf %s" % (os.path.split(url)[-1]))
                with cd("arff-%s" % version):
                    result = install_cmd('python setup.py install')
        if result.return_code == 0:
            print "----- ARFF %s installed -----" % version
            return True
        return False
    
    def resolve_dependencies(self, install=True):
        """Check if tool dependencies are installed. If the optional 'install'
        parameter is True, try to install any missing dependencies."""
        print "Resolving dependencies for %s..." % self.tool_env['pkg_name']
        if not self.install_arff():
            print "ERROR: Could not install ARFF"
            return False
        parent_env = {'parent': self.tool_env}
        ret_env = {}
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
        self.tool_env['dependencies_ok'] = True
        return True
    
    def set_env(self, local_env={}):
        """ Setup the values for the global environment for the current tool. """
        self.tool_env['pkg_name'] = 'easysvm'
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
            self.tool_env['version'] = "0.1"
        if not self.tool_env.has_key('url'):
            self.tool_env['url'] = "https://svn.tuebingen.mpg.de/ag-raetsch/external/ngs_galaxy_tue/stable/easysvm/"
        self.tool_env['install_dir'] = os.path.join(self.tool_env['install_dir_root'], self.tool_env['pkg_name'], self.tool_env['version'])
        common.setup_install_dir(self.tool_env)
        self.tool_env['env_set'] = True
        return True
    
    def is_installed(self, local_env={}, install_dependencies=False):
        """ Check if the current tool and its dependencies are installed. Optionally, 
        missing dependencies may be installed."""
        # TODO
        if not self.tool_env['dependencies_ok']:
            if not self.resolve_dependencies(install=install_dependencies):
                return False        
        print "No test for EasySVM exists yet"
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
                    # TODO: Get complete file name list
                    files = ['datagen.py']
                    install_cmd = sudo if self.tool_env['use_sudo'] else run
                    if not exists(self.tool_env['install_dir']):
                        install_cmd("mkdir -p %s" % self.tool_env['install_dir'])
                        install_cmd("chown %s %s" % (self.tool_env['user'], self.tool_env['install_dir']))
                    with nested(cd(self.tool_env['install_dir']), settings(hide('stdout'))):
                        for f in files:
                            install_cmd("wget --no-check-certificate %s" % (self.tool_env['url']+f))
                    if self.tool_env.has_key('shogun_env_script') and exists(self.tool_env['shogun_env_script']):
                        install_cmd("echo '. %s' > %s/env.sh" % (self.tool_env['shogun_env_script'], self.tool_env['install_dir']))
                        install_cmd("chmod +x %s/env.sh" % self.tool_env['install_dir'])
                        install_cmd('chown -R %s %s' % (env.user, self.tool_env['install_dir']))
                    else:
                        print "ERROR: Required dependency file for not found (for Shogun)."
                        self.tool_env['fatal_error'] = True
            # Make sure the tool installed correctly
            if not self.tool_env['fatal_error'] and (self.tool_env['installed'] or self.is_installed()):
                return common.compose_successful_return(self.tool_env)
        print "----- Problem installing EasySVM -----"
        return self.tool_env
    

instance = EasySVM()
util.add_class_methods_as_module_level_functions_for_fabric(instance, __name__)
