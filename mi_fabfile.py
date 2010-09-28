"""Fabric deployment file to set up a Galaxy AMI and/or update Galaxy source code
on an external EBS volume. Currently, targeted at Amazon's EC2 (http://aws.amazon.com/ec2/)

Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f mi_fabfile.py -H servername -i full_path_to_private_key_file <configure_MI | rebundle | update_galaxy_code>
"""
import os, os.path, time, contextlib, urllib
import datetime as dt
from contextlib import contextmanager
try:
    boto = __import__("boto")
    from boto.ec2.connection import EC2Connection
    from boto.s3.connection import S3Connection
    from boto.s3.key import Key
    from boto.exception import EC2ResponseError, S3ResponseError
    from boto.ec2.blockdevicemapping import BlockDeviceType, BlockDeviceMapping
except:
    boto = None

from fabric.api import *
from fabric.contrib.files import *
from fabric.contrib.console import confirm

# -- Specific setup for the Galaxy Cloud AMI

env.user = 'ubuntu'
env.use_sudo = True
env.path = '/mnt/galaxyTools/galaxy-central'
env.install_dir = '/opt/galaxy/pkg'
env.tmp_dir = "/mnt"
env.galaxy_files = '/mnt/galaxy'
env.shell = "/bin/bash -l -c"
env.use_sudo = True 

AMI_DESCRIPTION = "Base Galaxy on Ubuntu 10.04" # Value used for AMI description field

# == Templates
sge_request = """
-b no
-shell yes
-v PATH=/opt/sge/bin/lx24-amd64:/opt/galaxy/bin:/mnt/galaxyTools/tools/bin:/mnt/galaxyTools/tools/pkg/fastx_toolkit_0.0.13:/mnt/galaxyTools/tools/pkg/bowtie-0.12.5:/mnt/galaxyTools/tools/pkg/samtools-0.1.7_x86_64-linux:/mnt/galaxyTools/tools/pkg/gnuplot-4.4.0/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
"""

cm_upstart = """
description     "Start CloudMan contextualization script"

start on runlevel [2345]

task
exec python %s/ec2autorun.py
"""

welcome_msg_template = """
#!/bin/sh
echo
echo "Welcome to Galaxy CloudMan!"
echo " * Documentation:  http://galaxyproject.org/cloud"
"""

 
# == Decorators and context managers

def _if_not_installed(pname): 
    def argcatcher(func):
        def decorator(*args, **kwargs):
            # with settings(hide('warnings', 'running', 'stdout', 'stderr'),
            #         warn_only=True):
            with settings(warn_only=True):
                result = run(pname)
            if result.return_code == 127:
                print "'%s' not installed; return code: '%s'" % (pname, result.return_code)
                return func(*args, **kwargs)
            print "'%s' is already installed; return code: '%s'" % (pname, result.return_code)
        return decorator
    return argcatcher

def _if_installed(pname):
    """Run if the given program name is installed.
    """
    def argcatcher(func):
        def decorator(*args, **kwargs):
            with settings(
                    hide('warnings', 'running', 'stdout', 'stderr'),
                    warn_only=True):
                result = run(pname)
            if result.return_code in [0, 1]: 
                return func(*args, **kwargs)
        return decorator
    return argcatcher

@contextmanager
def _make_tmp_dir():
    work_dir = os.path.join(env.tmp_dir, "tmp")
    if not exists(work_dir):
        sudo("mkdir %s" % work_dir)
        sudo("chown %s %s" % (env.user, work_dir))
    yield work_dir
    if exists(work_dir):
        sudo("rm -rf %s" % work_dir)

# -- Fabric instructions

def configure_MI():
    """
    Configure the base Machine Image (MI) to be used with Galaxy Cloud:
    http://usegalaxy.org/cloud
    http://userwww.service.emory.edu/~eafgan/projects.html
    """
    time_start = dt.datetime.utcnow()
    print "Configuring host '%s'. Start time: %s" % (env.hosts[0], time_start)
    _update_system()
    _required_packages()
    _setup_users()
    _required_programs()
    _required_libraries()
    _configure_environment() 
    answer = confirm("Would you like to bundle this instance into a new machine image?", default=False)
    if answer:
        rebundle()
    time_end = dt.datetime.utcnow()
    print "Duration of machine configuration: %s" % str(time_end-time_start)

# == system

def _update_system():
    """Runs standard system update"""
    sudo('apt-get -y update')
    run('export DEBIAN_FRONTEND=noninteractive; sudo -E apt-get upgrade -y') # Ensure a completely noninteractive upgrade
    sudo('apt-get -y dist-upgrade')

# == packages

def _required_packages():
    """Install needed packages using apt-get"""
    packages = ['stow', 
                'xfsprogs', 
                'unzip', 
                'gcc', 
                'g++', 
                'nfs-kernel-server', 
                'zlib1g-dev', 
                'libssl-dev', 
                'libpcre3-dev', 
                'libreadline5-dev', 
                'rabbitmq-server',
                'git-core',
                'mercurial', 
                'subversion',
                'postgresql',
                'gfortran',
                'libsparsehash-dev' ] # Pull from outside (e.g., yaml file)?
    for package in packages:
        sudo("apt-get -y --force-yes install %s" % package)

# == users

def _setup_users():
    _add_user('galaxy')
    _add_user('sgeadmin')
    _add_user('postgres')
        
def _add_user(username):
    """ Add user with username to the system """
    if not contains(username, '/etc/passwd'):
        print "User '%s' not found, adding it now" % username
        sudo('useradd -d /home/%s --create-home --shell /bin/bash -c"Galaxy-required user" %s' % (username, username))
        print "Added user '%s'" % username
        
# == required programs

def _required_programs():
    """ Install required programs """
    if not exists(env.install_dir):
        sudo("mkdir -p %s" % env.install_dir)
        sudo("chown %s %s" % (env.user, env.install_dir))

    # Setup global environment for all users
    install_dir = os.path.split(env.install_dir)[0]
    append("export PATH=%s/bin:%s/sbin:$PATH" % (install_dir, install_dir), '/etc/bash.bashrc', use_sudo=True)
    append("export LD_LIBRARY_PATH=%s/lib" % install_dir, '/etc/bash.bashrc', use_sudo=True)
    # Install required programs
    _get_sge()
    _install_nginx()
    # _install_postgresql()
    _configure_postgresql()
    _install_setuptools()
    _install_openmpi()
    
def _get_sge():
    url = "http://userwww.service.emory.edu/~eafgan/content/ge62u5_lx24-amd64.tar.gz"
    install_dir = env.install_dir
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            sudo("chown %s %s" % (env.user, install_dir))
            run("tar -C %s -xvzf %s" % (install_dir, os.path.split(url)[1]))
            print "----- SGE downloaded and extracted to '%s' -----" % install_dir
    
# @_if_not_installed("nginx") # FIXME: this call is actually going to start nginx and never return...
def _install_nginx():
    upload_module_version = "2.0.12"
    url = "http://www.grid.net.ru/nginx/download/nginx_upload_module-%s.tar.gz" % upload_module_version
    install_dir = env.install_dir
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            # Maybe this can be untared to tmp dir and removed after installation?
            sudo("chown %s %s" % (env.user, install_dir))
            run("tar -C %s -xvzf %s" % (install_dir, os.path.split(url)[1]))
            print "----- nginx upload module downloaded and extracted to '%s' -----" % install_dir
    
    version = "0.7.67"
    url = "http://nginx.org/download/nginx-%s.tar.gz" % version
    install_dir = os.path.join(env.install_dir, "nginx")
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            run("tar xvzf %s" % os.path.split(url)[1])
            with cd("nginx-%s" % version):
                run("./configure --prefix=%s --with-ipv6 --add-module=%s/nginx_upload_module-%s --user=galaxy --group=galaxy --with-http_ssl_module --with-http_gzip_static_module" % (install_dir, env.install_dir, upload_module_version))
                run("make")
                sudo("make install")
                sudo("cd %s; stow nginx" % env.install_dir)
                
    nginx_conf_file = 'nginx.conf'
    if os.path.exists(nginx_conf_file):
        remote_conf_dir = os.path.join(install_dir, "conf", nginx_conf_file)
        put(nginx_conf_file, '/tmp/%s' % nginx_conf_file)
        sudo('mv /tmp/%s %s' % (nginx_conf_file, remote_conf_dir))
    else:
        print "ERROR: failed to find local configuration file '%s' for nginx" % nginx_conf_file

    nginx_errdoc_file = 'nginx_errdoc.tar.gz'
    if os.path.exists(nginx_errdoc_file):
        put(nginx_errdoc_file, '/tmp/%s' % nginx_errdoc_file)
        remote_errdoc_dir = os.path.join(install_dir, "html") 
        sudo('mv /tmp/%s %s/%s' % (nginx_errdoc_file, remote_errdoc_dir, nginx_errdoc_file))
        with cd(remote_errdoc_dir):
            sudo('tar xvzf %s' % nginx_errdoc_file)
        print "----- nginx installed and configured -----"
    else:
        print "ERROR: failed to find local error doc file '%s' for nginx" % nginx_errdoc_file
    
@_if_not_installed("pg_ctl")
def _install_postgresql():
    version = "8.4.4"
    url = "http://wwwmaster.postgresql.org/redir/198/h/source/v%s/postgresql-%s.tar.gz" % (version, version)
    install_dir = os.path.join(env.install_dir, "postgresql")
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            run("tar xvzf %s" % os.path.split(url)[1])
            with cd("postgresql-%s" % version):
                run("./configure --prefix=%s" % install_dir)
                with settings(hide('stdout')):
                    print "Making PostgreSQL..."
                    run("make")
                sudo("make install")
                sudo("cd %s; stow postgresql" % env.install_dir)
                print "----- PostgreSQL installed -----"
                
def _configure_postgresql(delete_main_dbcluster=False):
    """ This method is intended for cleaning up the installation when
    PostgreSQL is installed from a package. Basically, when PostgreSQL 
    is installed from a package, it creates a default database cluster 
    and splits the config file away from the data. 
    This method can delete the default database cluster that was automatically
    created when the package is installed. Deleting the main database cluster 
    also has the effect of stopping the auto-start of the postmaster server at 
    machine boot. The method adds all of the PostgreSQL commands to the PATH.
    """
    pg_ver = sudo("dpkg -s postgresql | grep Version | cut -f2 -d' ' | cut -f1 -d'-' | cut -f1-2 -d'.'")
    if delete_main_dbcluster:
        sudo('su postgres -c"pg_dropcluster --stop %s main"' % pg_ver)
    append("export PATH=/usr/lib/postgresql/%s/bin:$PATH" % pg_ver, "/etc/bash.bashrc", use_sudo=True)
    print "----- PostgreSQL configured -----"
    
@_if_not_installed("easy_install")
def _install_setuptools():
    version = "0.6c11"
    python_version = "2.6"
    url = "http://pypi.python.org/packages/%s/s/setuptools/setuptools-%s-py%s.egg#md5=bfa92100bd772d5a213eedd356d64086" % (python_version, version, python_version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            sudo("sh %s" % os.path.split(url)[1].split('#')[0])
            print "----- setuptools installed -----"

def _install_openmpi():
    version = "1.4.2"
    url = "http://www.open-mpi.org/software/ompi/v1.4/downloads/openmpi-%s.tar.gz" % version
    install_dir = os.path.join(env.install_dir, "openmpi")
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            run("tar xvzf %s" % os.path.split(url)[1])
            with cd("openmpi-%s" % version):
                run("./configure --prefix=%s --with-sge --enable-orterun-prefix-by-default" % install_dir)
                with settings(hide('stdout')):
                    print "Making OpenMPI..."
                    sudo("make all install")
                    sudo("cd %s; stow openmpi" % env.install_dir)
                    # append("export PATH=%s/bin:$PATH" % install_dir, "/etc/bash.bashrc", use_sudo=True)
                print "----- OpenMPI installed to %s -----" % install_dir

# == libraries
 
def _required_libraries():
    """Install pyhton libraries"""
    # Libraries to be be installed using easy_install
    libraries = ['simplejson', 'amqplib', 'pyyaml', 'mako', 'paste', 'routes', 'webhelpers', 'pastescript', 'webob']
    for library in libraries:
        sudo("easy_install %s" % library)
        
    _install_boto()

# @_if_not_installed # FIXME: check if boto is installed or just enable installation of an updated version
def _install_boto():
    install_dir = env.install_dir + "/boto"
    with contextlib.nested(cd(env.install_dir), settings(hide('stdout'))):
        sudo("git clone http://github.com/boto/boto.git")
        with cd(install_dir):
            sudo("python setup.py install")
            print("----- boto installed -----")

# == environment

def _configure_environment():
    _configure_ec2_autorun()
    _clean_rabbitmq_env()
    _configure_sge()
    _configure_galaxy_env()
    _configure_nfs()
    _configure_bash()
    
def _configure_ec2_autorun():
    # url = "http://userwww.service.emory.edu/~eafgan/content/ec2autorun"
    # with cd("/etc/init.d"):
    #     sudo("wget %s" % url)
    #     sudo("chmod u+x %s" % os.path.split(url)[1])
    #     sudo("update-rc.d %s defaults 80 15" % os.path.split(url)[1])
    #     print "----- ec2_autorun added -----"
    url = "http://userwww.service.emory.edu/~eafgan/content/ec2autorun.py"
    with cd(env.install_dir):
        sudo("wget %s" % url)
    # Create upstart configuration file for boot-time script
    cloudman_boot_file = 'cloudman.conf'
    f = open( cloudman_boot_file, 'w' )
    print >> f, cm_upstart % env.install_dir
    f.close()
    put(cloudman_boot_file, '/tmp/%s' % cloudman_boot_file) # Because of permissions issue
    sudo("mv /tmp/%s /etc/init/%s; chown root:root /etc/init/%s" % (cloudman_boot_file, cloudman_boot_file, cloudman_boot_file))
    os.remove(cloudman_boot_file)
    print "----- ec2_autorun added to upstart -----"

def _clean_rabbitmq_env():
    """
    RabbitMQ fails to start if its database is embedded into the image because it saves the current
    IP address or host name so delete it now. When starting up, RabbitMQ will recreate that directory.
    """
    sudo('/etc/init.d/rabbitmq-server stop')
    if exists('/var/lib/rabbitmq/mnesia'):
        print "Deleting old RabbitMQ database directory at '/var/lib/rabbitmq/mnesia'"
        sudo('rm -rf /var/lib/rabbitmq/mnesia')
    if exists('/mnesia'):
        print "Deleting old RabbitMQ database directory at '/mnesia'"
        sudo('rm -rf /mnesia')
        
def _configure_sge():
    """This method only sets up the environment for SGE w/o actually setting up SGE"""
    sge_root = '/opt/sge'
    if not exists(sge_root):
        sudo("mkdir -p %s" % sge_root)
        sudo("chown sgeadmin:sgeadmin %s" % sge_root)

def _configure_galaxy_env():
    # Edit the galaxy user .bash_profile in a somewhat roundabout way
    append('export TEMP=/mnt/galaxyData/tmp', '/home/galaxy/.bash_profile', use_sudo=True)
    sudo('chown galaxy:galaxy /home/galaxy/.bash_profile')
    # Create .sge_request file in galaxy home. This will be needed for proper execution of SGE jobs
    SGE_request_file = 'sge_request'
    f = open( SGE_request_file, 'w' )
    print >> f, sge_request
    f.close()
    put(SGE_request_file, '/tmp/%s' % SGE_request_file) # Because of permissions issue
    sudo("mv /tmp/%s /home/galaxy/.%s; chown galaxy:galaxy /home/galaxy/.%s" % (SGE_request_file, SGE_request_file, SGE_request_file))
    os.remove(SGE_request_file)
    
def _configure_nfs():
    exports = [ '/opt/sge           *(rw,sync,no_root_squash,no_subtree_check)', 
                '/mnt/galaxyData    *(rw,sync,no_root_squash,subtree_check,no_wdelay)',
                '/mnt/galaxyIndices *(rw,sync,no_root_squash,no_subtree_check)',
                '/mnt/galaxyTools   *(rw,sync,no_root_squash,no_subtree_check)',
                '%s/openmpi         *(rw,sync,no_root_squash,no_subtree_check)' % env.install_dir]
    append(exports, '/etc/exports', use_sudo=True)
    
def _configure_bash():
    """Some convenience/preference settings"""
    # Set instance login welcome message
    welcome_msg_template_file = '10-help-text'
    f = open( welcome_msg_template_file, 'w' )
    print >> f, welcome_msg_template
    f.close()
    put(welcome_msg_template_file, '/tmp/%s' % welcome_msg_template_file) # Because of permissions issue
    sudo("mv /tmp/%s /etc/update-motd.d/%s; chown root:root /etc/update-motd.d/%s" % (welcome_msg_template_file, welcome_msg_template_file, welcome_msg_template_file))
    os.remove(welcome_msg_template_file)
    
    append(['alias lt=\"ls -ltr\"', 'alias mroe=more'], '/etc/bash.bashrc', use_sudo=True)
    
def update_galaxy_code():
    """Pull the latest Galaxy code from bitbucket and update
    In order for this to work, an GC master instance on EC2 needs to be running
    with a volume where galaxy is stored attached. The script will then update
    Galaxy source and create a new snapshot.
    This script may also be used when updating tools that are also stored on the
    same volume as Galaxy.  
    """
    galaxy_home = "/mnt/galaxyTools/galaxy-central"
    if exists("%s/paster.pid" % galaxy_home):
        sudo('su galaxy -c "cd %s; sh run.sh --stop-daemon"' % galaxy_home)
    
    # Because of a conflict in static/welcome.html file on cloud Galaxy and the
    # main Galaxy repository, force local change to persist in case of a merge
    sudo('su galaxy -c "cd %s; hg --config ui.merge=internal:local pull --update"' % galaxy_home)
    commit_num = sudo('su galaxy -c "cd %s; hg tip | grep changeset | cut -d: -f2 "' % galaxy_home).strip()
    # A vanilla datatypes_conf is used so to make sure it's up to date delete it; setup.sh will recreate it.
    sudo('cd %s; rm datatypes_conf.xml' % galaxy_home)
    sudo('su galaxy -c "cd %s; sh setup.sh"' % galaxy_home)
    sudo('su galaxy -c "cd %s; sh manage_db.sh upgrade"' % galaxy_home)

    # Clean up galaxy directory before snapshoting
    with settings(warn_only=True):
        if exists("%s/paster.log" % galaxy_home):
            sudo("rm %s/paster.log" % galaxy_home)
        sudo("rm %s/database/pbs/*" % galaxy_home)
    # This should not be linked to where it's linked...
    if exists("%s/universe_wsgi.ini.cloud" % galaxy_home):
        sudo("rm %s/universe_wsgi.ini.cloud" % galaxy_home)
    sudo('su galaxy -c "cd %s; wget http://s3.amazonaws.com/cloudman/universe_wsgi.ini.cloud"' % galaxy_home)

    # Create a new snapshot of external volume
    if boto:
        # EDIT FOLLOWING LINE IF NEEDED/DESIRED:
        # Either set the following two environment variables or provide credentials info in the constructor:
        # AWS_ACCESS_KEY_ID - Your AWS Access Key ID
        # AWS_SECRET_ACCESS_KEY - Your AWS Secret Access Key
        # ec2_conn = EC2Connection('<aws access key>', '<aws secret key>')
        ec2_conn = EC2Connection()
        
        # hostname = env.hosts[0] # -H flag to fab command sets this variable so get only 1st hostname
        instance_id = run("curl --silent http://169.254.169.254/latest/meta-data/instance-id")
        # In lack of a better method... ask the user
        # vol_id = raw_input("What is the volume ID where Galaxy is stored (should be the one attached as device /dev/sdg)? ")
        vol_list = ec2_conn.get_all_volumes()
        # Detect the volume ID of EBS volume where Galaxy is installed:
        # - find out what device is galaxyTools mounted to
        # - then search for given volume    
        device_id = sudo("df | grep '%s' | awk '{print $1}'" % os.path.split(galaxy_home)[0])
        print "Detected device '%s' as being the one where Galaxy is stored" % device_id
        galaxy_tools_vol = None
        for vol in vol_list:
            if vol.attach_data.instance_id==instance_id and vol.attach_data.status=='attached' and vol.attach_data.device == device_id:
                galaxy_tools_vol = vol
        if galaxy_tools_vol:
            sudo("umount %s" % os.path.split(galaxy_home)[0])
            _detach(ec2_conn, instance_id, galaxy_tools_vol.id)
            desc = "Galaxy (at commit %s) and tools" % commit_num
            snap_id = _create_snapshot(ec2_conn, galaxy_tools_vol.id, desc)
            print "--------------------------"
            print "New snapshot ID: %s" % snap_id
            # print "Don't forget to update the file 'snaps-latest.txt' in 'galaxy-snapshots' bucket on S3 with the following line:"
            # print "TOOLS=%s|%s" % (snap_id, str(galaxy_tools_vol.size))
            print "--------------------------"
            answer = confirm("Would you like to update the file 'snaps-latest.txt' in 'galaxy-snapshots' bucket on S3 with the following line: 'TOOLS=%s|%s'" % (snap_id, str(galaxy_tools_vol.size)))
            if answer:
                _update_snaps_latest_file(snap_id, galaxy_tools_vol.size, commit_num='Galaxy at commit %s' % commit_num)
            
            answer = confirm("Would you like to make the newly created snapshot '%s' public (you should if snaps-latest.txt in the previous question was updated)?" % snap_id)
            if answer:
                ec2_conn.modify_snapshot_attribute(snap_id, attribute='createVolumePermission', operation='add', groups=['all'])
            
            answer = confirm("Would you like to attach the *old* volume '%s' (but with updated Galaxy) used to make the new snapshot back to instance '%s' and mount it?" % (galaxy_tools_vol.id, instance_id))
            if answer:
                _attach(ec2_conn, instance_id, galaxy_tools_vol.id, device_id)
                sudo("mount %s %s" % (device_id, os.path.split(galaxy_home)[0]))
                _start_galaxy()
            elif confirm("Would you like to create a new volume from the *new* snapshot '%s', attach it to the instance '%s' and mount it?" % (snap_id, instance_id)):
                try:
                    new_vol = ec2_conn.create_volume(galaxy_tools_vol.size, galaxy_tools_vol.zone, snapshot=snap_id)
                    print "Created new volume of size '%s' from snapshot '%s' with ID '%s'" % (new_vol.size, snap_id, new_vol.id)
                    _attach(ec2_conn, instance_id, new_vol.id, device_id)
                    sudo("mount %s %s" % (device_id, os.path.split(galaxy_home)[0]))
                    _start_galaxy()
                except EC2ResponseError, e:
                    print "Error creating volume: %s" % e
            print "----- Done updating Galaxy code -----"
        else:
            print "ERROR: Unable to 'discover' Galaxy volume id"

def _start_galaxy():
    answer = confirm("Would you like to start Galaxy on instance?")
    if answer:
        sudo('su galaxy -c "source /etc/bash.bashrc; source /home/galaxy/.bash_profile; export SGE_ROOT=/opt/sge; cd /mnt/galaxyTools/galaxy-central; sh run.sh --daemon"')

def _update_snaps_latest_file(snap_id, vol_size, **kwargs):
    bucket_name = 'galaxy-snapshots'
    remote_file_name = 'snaps-latest.txt'
    remote_url = 'http://s3.amazonaws.com/%s/%s' % (bucket_name, remote_file_name)
    downloaded_local_file = "downloaded_snaps-latest.txt"
    old_remote_file = generated_local_file = "snaps-latest.txt"
    urllib.urlretrieve(remote_url, downloaded_local_file)
    local("sed 's/^TOOLS.*/TOOLS=%s|%s/' %s > %s" % (snap_id, vol_size, downloaded_local_file, generated_local_file))
    # Rename current old_remote_file to include date it was last modified
    date_uploaded = _get_date_file_last_modified_on_S3(bucket_name, old_remote_file)
    new_name_for_old_snaps_file = "snaps-%s.txt" % date_uploaded
    _rename_file_in_S3(new_name_for_old_snaps_file, bucket_name, old_remote_file)
    # Save the new file to S3
    return _save_file_to_bucket(bucket_name, remote_file_name, generated_local_file, **kwargs)

def _get_bucket(bucket_name):
    s3_conn = S3Connection()
    b = None
    for i in range(0, 5):
		try:
			b = s3_conn.get_bucket(bucket_name)
			break
		except S3ResponseError: 
			print "Bucket '%s' not found, attempt %s/5" % (bucket_name, i)
			return None
    return b

def _get_date_file_last_modified_on_S3(bucket_name, file_name):
    """Return date file_name was last modified in format YYYY-MM-DD"""
    b = _get_bucket(bucket_name)
    if b is not None:
        try:
            k = b.get_key(file_name)
            lm = k.last_modified
            mlf = time.strptime(lm, "%a, %d %b %Y %H:%M:%S GMT")
            return time.strftime("%Y-%m-%d", mlf)
        except S3ResponseError, e:
            print "Failed to get file '%s' from bucket '%s': %s" % (file_name, bucket_name, e)
            return ""
    	except ValueError, e:
            print "Failed to format file '%s' last modified: %s" % (file_name, e)
    	    return ""

def _rename_file_in_S3(new_key_name, bucket_name, old_key_name):
    b = _get_bucket(bucket_name)
    if b is not None:
        try:
            k = b.get_key(old_key_name) # copy any metadata too
            b.copy_key(new_key_name, bucket_name, old_key_name, metadata=k.metadata, preserve_acl=True)
            print "Successfully renamed file '%s' in bucket '%s' to '%s'." % (old_key_name, bucket_name, new_key_name)
            return True
        except S3ResponseError, e:
    	     print "Failed to rename file '%s' in bucket '%s' as file '%s': %s" % (old_key_name, bucket_name, new_key_name, e)
    	     return False
	
def _save_file_to_bucket(bucket_name, remote_filename, local_file, **kwargs):
    """ Save the local_file to bucket_name as remote_filename. Also, any additional
    arguments passed as key-value pairs, are stored as file's metadata on S3."""
    # print "Establishing handle with bucket '%s'..." % bucket_name
    b = _get_bucket(bucket_name)
    if b is not None:
        # print "Establishing handle with key object '%s'..." % remote_filename
        k = Key( b, remote_filename )
        print "Attempting to save file '%s' to bucket '%s'..." % (remote_filename, bucket_name)
        try:
            # Store some metadata (key-value pairs) about the contents of the file being uploaded
            # Note that the metadata must be set *before* writing the file
            k.set_metadata('date_uploaded', str(dt.datetime.utcnow()))
            for args_key in kwargs:
                print "Adding metadata to file '%s': %s=%s" % (remote_filename, args_key, kwargs[args_key])
                k.set_metadata(args_key, kwargs[args_key])
            print "Saving file '%s'" % local_file
            k.set_contents_from_filename(local_file)
            print "Successfully added file '%s' to bucket '%s'." % (remote_filename, bucket_name)
            answer = confirm("Would you like to make file '%s' publicly readable?" % remote_filename)
            if answer:
                k.make_public()
        except S3ResponseError, e:
            print "Failed to save file local file '%s' to bucket '%s' as file '%s': %s" % ( local_file, bucket_name, remote_filename, e )
            return False
        return True
    else:
        return False
    
# == Machine image rebundling code

def rebundle():
    """
    Rebundles the EC2 instance that is passed as the -H parameter
    This script handles all aspects of the rebundling process and is (almost) fully automated.
    Two things should be edited and provided before invoking it: AWS account information 
    and the desired size of the root volume for the new instance.  
     
    TODO: Customization: /usr/bin/landscape-sysinfo, /etc/update-motd.d/
    :rtype: bool
    :return: If instance was successfully rebundled and an AMI ID was received,
             return True.
             False, otherwise.
    """
    time_start = dt.datetime.utcnow()
    print "Rebundling instance '%s'. Start time: %s" % (env.hosts[0], time_start)
    # After reboot, the autorun log file was probbaly created so remove it from the machine image
    if os.path.isfile('%s/ec2autorun.py.log' % env.install_dir):
        os.remove('%s/ec2autorun.py.log' % env.install_dir)
    if boto:
        # EDIT FOLLOWING TWO LINES IF NEEDED/DESIRED:
        # Either set the following two environment variables or provide credentials info in the constructor:
        # AWS_ACCESS_KEY_ID - Your AWS Access Key ID
        # AWS_SECRET_ACCESS_KEY - Your AWS Secret Access Key
        # * OR *
        # ec2_conn = EC2Connection('<aws access key>', '<aws secret key>')
        ec2_conn = EC2Connection()
        vol_size = 15 # This will be the size (in GB) of the root partition of the new image
        
        # hostname = env.hosts[0] # -H flag to fab command sets this variable so get only 1st hostname
        instance_id = run("curl --silent http://169.254.169.254/latest/meta-data/instance-id")
        
        # Handle reboot if required
        if _reboot(ec2_conn, instance_id):
            return False # Indicates that rebundling was not completed
            
        image_id = None
        availability_zone = run("curl --silent http://169.254.169.254/latest/meta-data/placement/availability-zone")
        kernel_id = run("curl --silent http://169.254.169.254/latest/meta-data/kernel-id")
        if instance_id and availability_zone and kernel_id:
            print "Rebundling instance with ID '%s'" % instance_id
            try:
                vol = ec2_conn.create_volume(vol_size, availability_zone)
                # TODO: wait until it becomes 'available'
                print "Created new volume of size '%s' with ID '%s'" % (vol_size, vol.id)
            except EC2ResponseError, e:
                print "Error creating volume: %s" % e
                return False
            
            if vol:
                try:
                    # Attach newly created volume to the instance
                    dev_id = '/dev/sdh'
                    if not _attach(ec2_conn, instance_id, vol.id, dev_id):
                        print "Error attaching volume to the instance. Aborting."
                        return False
                    # Move the file system onto the new volume
                    # TODO: This should be downloaded from elsewhere
                    url = 'http://userwww.service.emory.edu/~eafgan/content/instance-to-ebs-ami.sh'
                    # with contextlib.nested(cd('/tmp'), settings(hide('stdout', 'stderr'))):
                    with cd('/tmp'):
                        if exists('/tmp/'+os.path.split(url)[1]):
                            sudo('rm /tmp/'+os.path.split(url)[1])
                        sudo('wget %s' % url)
                        sudo('chmod u+x /tmp/%s' % os.path.split(url)[1])
                        sudo('./%s' % os.path.split(url)[1])
                    # Detach the new volume
                    _detach(ec2_conn, instance_id, vol.id)
                    answer = confirm("Would you like to terminate the instance used during rebundling?", default=False)
                    if answer:
                        ec2_conn.terminate_instances([instance_id])
                    # Create a snapshot of the new volume
                    commit_num = local('cd %s; hg tip | grep changeset | cut -d: -f2' % os.getcwd()).strip()
                    snap_id = _create_snapshot(ec2_conn, vol.id, "AMI: galaxy-cloudman (using mi-deployment at commit %s)" % commit_num)
                    # Register the snapshot of the new volume as a machine image (i.e., AMI)
                    arch = 'x86_64'
                    root_device_name = '/dev/sda1'
                    # Extra info on how EBS image registration is done: http://markmail.org/message/ofgkyecjktdhofgz
                    # http://www.elastician.com/2009/12/creating-ebs-backed-ami-from-s3-backed.html
                    # http://www.shlomoswidler.com/2010/01/creating-consistent-snapshots-of-live.html
                    ebs = BlockDeviceType()
                    ebs.snapshot_id = snap_id
                    ephemeral0_device_name = '/dev/sdb'
                    ephemeral0 = BlockDeviceType()
                    ephemeral0.ephemeral_name = 'ephemeral0'
                    ephemeral1_device_name = '/dev/sdc'
                    ephemeral1 = BlockDeviceType()
                    ephemeral1.ephemeral_name = 'ephemeral1'
                    # ephemeral2_device_name = '/dev/sdd' # Needed for instances w/ 3 ephemeral disks
                    # ephemeral2 = BlockDeviceType()
                    # ephemeral2.ephemeral_name = 'ephemeral2'
                    # ephemeral3_device_name = '/dev/sde' # Needed for instances w/ 4 ephemeral disks
                    # ephemeral3 = BlockDeviceType()
                    # ephemeral3.ephemeral_name = 'ephemeral3'
                    block_map = BlockDeviceMapping()
                    block_map[root_device_name] = ebs
                    block_map[ephemeral0_device_name] = ephemeral0
                    block_map[ephemeral1_device_name] = ephemeral1
                    name = 'galaxy-cloudman-%s' % time_start.strftime("%Y-%m-%d")
                    image_id = ec2_conn.register_image(name, description=AMI_DESCRIPTION, architecture=arch, kernel_id=kernel_id, root_device_name=root_device_name, block_device_map=block_map)
                    answer = confirm("Volume with ID '%s' was created and used to make this AMI but is not longer needed. Would you like to delete it?" % vol.id)
                    if answer:
                        ec2_conn.delete_volume(vol.id)
                    print "--------------------------"
                    print "Finished creating new machine image. Image ID: '%s'" % (image_id)
                    print "MAKE SURE to upload a new contextualization script to the 'ssfg' bucket on S3 named 'customizeEC2instance_%s.zip' AND (if this AMI is to be made public) give it read permission for everyone." % image_id
                    print "--------------------------"
                    answer = confirm("Would you like to make this machine image public?", default=False)
                    if image_id and answer:
                        ec2_conn.modify_image_attribute(image_id, attribute='launchPermission', operation='add', groups=['all'])
                except EC2ResponseError, e:
                    print "Error creating image: %s" % e
                    return False
            else:
                print "Error creating new volume"
                return False
        else:
            print "Error retrieving instance availability zone"
            return False            
    else:
        print "Python boto library not available. Aborting."
        return False
    time_end = dt.datetime.utcnow()
    print "Duration of instance rebundling: %s" % str(time_end-time_start)
    if image_id is not None:
        return True
    else:
        return False

def _reboot(ec2_conn, instance_id, force=False):
    """
    Reboot current instance if required. Reboot can be forced by setting the 
    method's 'force' parameter to True.
    
    :rtype: bool
    :return: If instance was rebooted, return True. Note that this primarily 
             indicates if the instance was rebooted and does not guarantee that 
             the instance is accessible.
             False, otherwise.
    """
    if force or exists("/var/run/reboot-required"):
        answer = confirm("Before rebundling, instance '%s' needs to be rebooted. Reboot instance?" % instance_id)
        if answer and instance_id:
            print "Rebooting instance with ID '%s'" % instance_id
            try:
                ec2_conn.reboot_instances([instance_id])
                wait_time = 35
                # reboot(wait_time)
                print "Instance '%s' with IP '%s' rebooted. Waiting (%s sec) for it to come back up." % (instance_id, env.hosts[0], str(wait_time))
                time.sleep(wait_time)
                for i in range(30):
                    ssh = None
                    with settings(warn_only=True):
                        print "Checking ssh connectivity to instance '%s' (you may be prompted to confirm security credentials)" % env.hosts[0]
                        ssh = local('ssh -i %s %s@%s "exit"' % (env.key_filename[0], env.user, env.hosts[0]))
                    if ssh.return_code == 0:
                        print "\n--------------------------"
                        print "Machine '%s' is alive" % env.hosts[0]
                        print "This script will exit now. Invoke it again while passing method name 'rebundle' as the last argument to the fab script."
                        print "--------------------------\n"
                        return True
                    else:
                        print "Still waiting..."
                        time.sleep(3)
                    if i == 29:
                        print "Machine '%s' did not respond for while now, aborting" % env.hosts[0]
                        return True
            except EC2ResponseError, e:
                print("Error rebooting instance '%s' with IP '%s': %s" % (instance_id, env.hosts[0], e))
                return False
            except Exception, e:
                print("Error rebooting instance '%s' with IP '%s': %s" % (instance_id, env.hosts[0], e))
                print("Try running this script again with 'rebundle' as the last argument.")
                return False
        else:
            print "Cannot rebundle without instance reboot. Aborting rebundling."
            return False

def _attach( ec2_conn, instance_id, volume_id, device ):
    """
    Attach EBS volume to the given device (using boto).
    Try it for some time.
    """
    try:
        print "Attaching volume '%s' to instance '%s' as device '%s'" % ( volume_id, instance_id, device )
        volumestatus = ec2_conn.attach_volume( volume_id, instance_id, device )
    except EC2ResponseError, e:
        print "Attaching volume '%s' to instance '%s' as device '%s' failed. Exception: %s" % ( volume_id, instance_id, device, e )
        return False

    for counter in range( 30 ):
        print "Attach attempt %s, volume status: %s" % ( counter, volumestatus )
        if volumestatus == 'attached':
            print "Volume '%s' attached to instance '%s' as device '%s'" % ( volume_id, instance_id, device )
            break
        if counter == 29:
            print "Volume '%s' FAILED to attach to instance '%s' as device '%s'. Aborting." % ( volume_id, instance_id, device )
            return False

        volumes = ec2_conn.get_all_volumes( [volume_id] )
        volumestatus = volumes[0].attachment_state()
        time.sleep( 3 )
    return True

def _detach( ec2_conn, instance_id, volume_id ):
    """
    Detach EBS volume from the given instance (using boto).
    Try it for some time.
    """
    try:
        volumestatus = ec2_conn.detach_volume( volume_id, instance_id, force=True )
    except EC2ResponseError, ( e ):
        print "Detaching volume '%s' from instance '%s' failed. Exception: %s" % ( volume_id, instance_id, e )
        return False

    for counter in range( 30 ):
        print "Volume '%s' status '%s'" % ( volume_id, volumestatus )
        if volumestatus == 'available':
            print "Volume '%s' successfully detached from instance '%s'." % ( volume_id, instance_id )
            break
        if counter == 29:
            print "Volume '%s' FAILED to detach to instance '%s'." % ( volume_id, instance_id )
        time.sleep( 3 )
        volumes = ec2_conn.get_all_volumes( [volume_id] )
        volumestatus = volumes[0].status

def _create_snapshot(ec2_conn, volume_id, description=None):
    """
    Create a snapshot of the EBS volume with the provided volume_id. 
    Wait until the snapshot process is complete (note that this may take quite a while)
    """
    print "Initiating snapshot of EBS volume '%s'" % volume_id
    snapshot = ec2_conn.create_snapshot(volume_id, description=description)
    if snapshot: 
        while snapshot.status != 'completed':
            print "Snapshot '%s' progress: '%s'; status: '%s'" % (snapshot.id, snapshot.progress, snapshot.status)
            time.sleep(6)
            snapshot.update()
        print "Creation of snapshot for volume '%s' completed: '%s'" % (volume_id, snapshot)
        return snapshot.id
    else:
        print "Could not create snapshot from volume with ID '%s'" % volume_id
        return False