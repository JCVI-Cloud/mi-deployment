from contextlib import contextmanager
from fabric.api import sudo, run, env
from fabric.contrib.files import exists, settings

@contextmanager
def make_tmp_dir(tool_env):
    """ Make a remote temporary directory. The location of the created dir is 
    defined in tool_env['work_dir'] and should be changed at the deployment
    configuration level. """
    env.user = tool_env['user']
    env.use_sudo = tool_env['use_sudo']
    work_dir = tool_env['work_dir']
    install_cmd = sudo if env.use_sudo else run
    if not exists(work_dir):
        install_cmd("mkdir %s" % work_dir)
        install_cmd("chown %s %s" % (env.user, work_dir))
    yield work_dir
    if exists(work_dir):
        install_cmd("rm -rf %s" % work_dir)

def install_required_packages(packages):
    """ Install needed packages using apt-get """
    for package in packages:
        sudo("apt-get -y --force-yes install %s" % package)

def install_required_python_libraries(tool_env, libraries):
    """ Install needed python libraries using pip """
    install_cmd = sudo if tool_env['use_sudo'] else run
    for lib in libraries: 
        install_cmd("pip install %s" % lib)
    
def compose_successful_return(tool_env):
    """ A convenience method that composes 'This tool has been installed' message. 
    The method also checks if a given tool is a dependency and, if so, includes a
    direct path to the installed tool's env.sh script under 'env_script' key of 
    the dictionary it returns. """
    env.user = tool_env['user']
    print "\n----- %s v%s installed %s -----\n" % (str(tool_env['pkg_name']).upper(), tool_env['version'], ('in ' + tool_env['install_dir']) if tool_env.has_key('install_dir') else 'at system level')
    # If dependency, return self environment for use by dependencies
    if tool_env['is_dependency'] and tool_env.has_key('install_dir'):
        env_script = "%s/env.sh" % tool_env['install_dir']
        if exists(env_script):
            tool_env['env_script'] = env_script
    return tool_env

def check_for_parent(tool_env, local_env):
    """ Convenience method for checking if a given tool has a parent tool (i.e., 
    currently being installed tool is a dependency) and extracting relevant values
    from parent's environment into the current tool's environment dictionary."""
    if local_env.has_key('parent'):
        tool_env['is_dependency'] = True
        tool_env['install_dir_root'] = local_env['parent']['install_dir_root']
        if local_env['parent'].has_key('galaxy_dir'):
            tool_env['galaxy_dir'] = local_env['parent']['galaxy_dir']
    elif tool_env.has_key('parent'):
        # Check if parent was already set
        tool_env['is_dependency'] = True
    else:
        tool_env['is_dependency'] = False
    return tool_env

def check_install_dir_root(tool_env):
    """ Check if 'install_dir_root' env field has been set and print an error 
    message if not. """
    if tool_env['install_dir_root'] == '':
        print "ERROR: Missing value for 'install_dir_root' that points to the root dir where this tool should be installed."
        return False
    return True

def check_galaxy(tool_env):
    """ Check if the 'galaxy_dir' env field is set and if the given directory 
    exists on the remote machine. If not, print an error message and return False."""
    env.user = tool_env['user']
    if tool_env['galaxy_dir'] == '' or not exists(tool_env['galaxy_dir']):
        print "ERROR: Missing a value or dir does not exist for environment key 'galaxy_dir' that points to Galaxy install dir."
        return False
    return True

def setup_install_dir(tool_env):
    """ Setup the installation dir for the current tool as defined in the 'install_dir'
    env field. This implies creating the directory and setting directory ownership 
    to the user installing the current tool. """
    env.user = tool_env['user']
    install_cmd = sudo if tool_env['use_sudo'] else run
    if not exists(tool_env['install_dir']):
        install_cmd("mkdir -p %s" % tool_env['install_dir'])
    install_cmd("chown %s %s" % (env.user, tool_env['install_dir']))

def merge_dicts(tool_env, local_env):
    """ Merge tool_env into local_env so values from tool_env take presedence """
    local_env.update(tool_env)
    return local_env

def cmd_success(tool_env, cmd):
    """Run given command and return True of return code is 0, False otherwise"""
    install_cmd = sudo if tool_env['use_sudo'] else run
    with settings(warn_only=True):
        result = install_cmd(cmd)
        if result.return_code == 0:
            return True
    return False
    