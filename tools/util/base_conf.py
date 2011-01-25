import os, common

class Deployment(object):
    """
    Define deployment configuration in this file and call it (by configuration 
    name) at the time of tool installation (see individual tool's invocation 
    command on how to do so).
    """
    def __init__(self, conf=None):
        self.work_dir = os.path.join('/tmp', "fab_tmp")
        self.tool_env={ 'galaxy_dir': '',
                        'install_dir_root': '',
                        'work_dir': self.work_dir,
                        'version': '',
                        'installed': False,
                        'env_set': False,
                        'user': '',
                        'use_sudo': False,
                        'fatal_error': False,
                        'dependencies_ok': False}
        # Enable composition of modules. Because each module needs to load the 
        # fabric configuration, as part of init, enable the conf to be loaded.
        self.conf = conf
        if self.conf is not None:
            func = getattr(self, conf)
            func()
    
    def get_env(self, local_env={}):
        if not self.tool_env['env_set']: 
            self.set_env(local_env)
        return common.compose_successful_return(self.tool_env)
    
    def ec2_ubuntu(self):
        print "Loading EC2 Ubuntu configuration"
        self.conf = "ec2_ubuntu"
        self.tool_env['user'] = 'ubuntu'
        self.tool_env['use_sudo'] = True
        self.tool_env['galaxy_dir'] = '/home/%s/galaxy-central' % self.tool_env['user']
        self.tool_env['install_dir_root'] = '/tmp/tools2'
    
    def emory_cloud(self):
        print "Loading Emory Cloud configuration"
        self.conf = "emory_cloud"
        self.tool_env['user'] = 'afgane'
        self.tool_env['use_sudo'] = True
        self.tool_env['galaxy_dir'] = '/home/%s/galaxy-central' % self.tool_env['user']
        self.tool_env['install_dir_root'] = '/tmp/tools2'
    
