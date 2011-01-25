"""
Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f <file_name>.py [-i <private_key>] -H <server> <deployment_conf> install
"""
# for Python 2.5
from __future__ import with_statement

from fabric.api import sudo, run, put, cd, env
from fabric.contrib.files import settings, hide
from contextlib import nested

from util import base_conf
from util import util
from util import common
import os, os.path, tempfile

test_script = """
from shogun.Features import StringCharFeatures, RAWBYTE
from numpy import array
strings=['hey','guys','i','am','a','string']
#create string features
f=StringCharFeatures(strings, RAWBYTE)
#replace string 0
f.set_feature_vector(array(['t','e','s','t']), 0)
#print "strings", f.get_features()
f.get_features(), f
"""

class Shogun(base_conf.Deployment):
    def __init__(self, conf=None):
        super(Shogun, self).__init__(conf)
    
    def set_env(self, local_env={}):
        """ Setup the values for the global environment for the current tool. """
        self.tool_env['pkg_name'] = 'shogun'
        print "Setting up environment for %s" % str(self.tool_env['pkg_name']).upper()
        if local_env != {}:
            self.tool_env = common.merge_dicts(self.tool_env, local_env)
        env.user = self.tool_env['user']
        self.tool_env = common.check_for_parent(self.tool_env, local_env)
        if not common.check_install_dir_root(self.tool_env):
            return False
        if self.tool_env['version'] == '':
            self.tool_env['version'] = "0.10.0"
        if self.tool_env.has_key('release'):
            print "Using provided Shogun release: '%s'" % self.tool_env['release']
            release = self.tool_env['release']
        else:
            release = '.'.join(self.tool_env['version'].split('.')[:2])
        if not self.tool_env.has_key('url'):
            self.tool_env['url'] = "http://shogun-toolbox.org/archives/shogun/releases/%s/sources/shogun-%s.tar.bz2" % (release, self.tool_env['version'])
        self.tool_env['install_dir'] = os.path.join(self.tool_env['install_dir_root'], self.tool_env['pkg_name'], self.tool_env['version'])
        common.setup_install_dir(self.tool_env)
        self.tool_env['env_set'] = True
        return True
    
    def is_installed(self, local_env={}, install_dependencies=False):
        """ Check if the current tool and its dependencies are installed. Optionally, 
        missing dependencies may be installed."""
        if not self.tool_env['env_set']: 
            self.set_env(local_env)
        print "Checking if %s %s is installed..." % (self.tool_env['pkg_name'], self.tool_env['version'])
        f = tempfile.NamedTemporaryFile()
        f.write(test_script)
        f.flush()
        with common.make_tmp_dir(self.tool_env) as work_dir:
            put(f.name, os.path.join(work_dir, 'features_string_char_modular.py'))
            f.close()
            with cd(work_dir):
                with settings(warn_only=True):
                    install_cmd = sudo if self.tool_env['use_sudo'] else run
                    result = install_cmd("source %s/env.sh; python features_string_char_modular.py" % self.tool_env['install_dir'])
                if result.return_code == 0:
                    self.tool_env['installed'] = True
                    print "%s %s is installed in %s" % (str(self.tool_env['pkg_name']).upper(), self.tool_env['version'], self.tool_env['install_dir'])
                    return True
        print "Shogun %s is not installed." % self.tool_env['version']
        return False
    
    def install(self, local_env={}):
        """ If not already installed, install given tool and all of its dependencies. """
        if self.set_env(local_env):
            if not self.is_installed(local_env=local_env):
                print "Trying to install %s %s as user %s" % (self.tool_env['pkg_name'], self.tool_env['version'], env.user)
                packages = ['gcc', 'g++', 'octave3.0-headers', 'python-dev', 'python-numpy', 
                    'liblapack-dev', 'libatlas3gf-base', 'python-numpy-ext', 'python-matplotlib',
                    'swig']
                common.install_required_packages(packages)
                install_cmd = sudo if self.tool_env['use_sudo'] else run
                with common.make_tmp_dir(self.tool_env) as work_dir:
                    with nested(cd(work_dir), settings(hide('stdout'))):
                        install_cmd("wget %s" % self.tool_env['url'])
                        install_cmd("tar xvjf %s" % os.path.split(self.tool_env['url'])[1])
                        with cd("shogun-%s/src" % self.tool_env['version']):
                            install_cmd("./configure --prefix=%s --interfaces=libshogun,libshogunui,python,python_modular,octave" % self.tool_env['install_dir'])
                            print "Making Shogun..."
                            install_cmd("make")
                            install_cmd("make install")
                install_cmd("echo 'export LD_LIBRARY_PATH=%s/lib:$LD_LIBRARY_PATH' > %s/env.sh" % (self.tool_env['install_dir'], self.tool_env['install_dir']))
                install_cmd("cd %s/lib; ln -s python* python" % self.tool_env['install_dir'])
                install_cmd("echo 'export PYTHONPATH=%s/lib/python/dist-packages:$PYTHONPATH' >> %s/env.sh" % (self.tool_env['install_dir'], self.tool_env['install_dir']))
                install_cmd("chmod +x %s/env.sh" % self.tool_env['install_dir'])
                install_dir_root = os.path.join(self.tool_env['install_dir_root'], self.tool_env['pkg_name'])
                install_cmd('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, self.tool_env['install_dir'], install_dir_root))
                install_cmd('chown -R %s:%s %s' % (env.user, env.user, install_dir_root))
            if self.tool_env['installed'] or self.is_installed():
                return common.compose_successful_return(self.tool_env)
        print "----- Problem installing Shogun -----"
        return self.tool_env
    

instance = Shogun()
util.add_class_methods_as_module_level_functions_for_fabric(instance, __name__)
