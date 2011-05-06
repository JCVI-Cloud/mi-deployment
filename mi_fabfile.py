"""Fabric deployment file to set up a Galaxy CloudMan AMI. 
Currently, targeted for Amazon's EC2 (http://aws.amazon.com/ec2/)

Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f mi_fabfile.py -i full_path_to_private_key_file -H servername <configure_MI[:do_rebundle] | rebundle>
"""
import os, os.path, time, contextlib, tempfile
import datetime as dt
from contextlib import contextmanager
try:
    boto = __import__("boto")
    from boto.ec2.connection import EC2Connection
    from boto.exception import EC2ResponseError
    from boto.ec2.blockdevicemapping import BlockDeviceType, BlockDeviceMapping
except:
    boto = None

# from fabric.api import *
# from fabric.contrib.files import *
from fabric.api import sudo, run, env, cd, put, local
from fabric.contrib.console import confirm
from fabric.contrib.files import exists, settings, hide, contains, append
from fabric.colors import red, green, yellow

AMI_DESCRIPTION = "Galaxy CloudMan on Ubuntu 10.04" # Value used for AMI description field
# -- Adjust this link if using content from another location
CDN_ROOT_URL = "http://userwww.service.emory.edu/~eafgan/content"
REPO_ROOT_URL = "https://bitbucket.org/afgane/mi-deployment/raw/tip"

# EDIT FOLLOWING TWO LINES IF NEEDED/DESIRED:
# If you do not have the following two environment variables set (AWS_ACCESS_KEY_ID,
# AWS_SECRET_ACCESS_KEY), provide your credentials below and uncomment the two lines:
# os.environ['AWS_ACCESS_KEY_ID'] = "your access key"
# os.environ['AWS_SECRET_ACCESS_KEY'] = "your secret key"


# -- Specific setup for the Galaxy Cloud AMI
env.user = 'ubuntu'
env.use_sudo = True
env.path = '/mnt/galaxyTools/galaxy-central'
env.install_dir = '/opt/galaxy/pkg'
env.tmp_dir = "/mnt"
env.galaxy_files = '/mnt/galaxy'
env.shell = "/bin/bash -l -c"
env.use_sudo = True
env.sources_file = "/etc/apt/sources.list"
env.std_sources = ["deb http://watson.nci.nih.gov/cran_mirror/bin/linux/ubuntu lucid/"]

# == Templates
sge_request = """
-b no
-shell yes
-v PATH=/opt/sge/bin/lx24-amd64:/opt/galaxy/bin:/mnt/galaxyTools/tools/bin:/mnt/galaxyTools/tools/pkg/fastx_toolkit_0.0.13:/mnt/galaxyTools/tools/pkg/bowtie-0.12.5:/mnt/galaxyTools/tools/pkg/samtools-0.1.7_x86_64-linux:/mnt/galaxyTools/tools/pkg/gnuplot-4.4.0/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
-v DISPLAY=:42
"""

cm_upstart = """
description     "Start CloudMan contextualization script"

start on runlevel [2345]
start on started rabbitmq-server

task
exec python %s/ec2autorun.py
"""

rabitmq_upstart = """
description "RabbitMQ Server"
author  "RabbitMQ"

start on runlevel [2345] 

stop on runlevel [01456]

exec /usr/sbin/rabbitmq-multi start_all 1 > /var/log/rabbitmq/startup_log 2> /var/log/rabbitmq/startup_err 
# respawn
"""

welcome_msg_template = """#!/bin/sh
echo
echo "Welcome to Galaxy CloudMan!"
echo " * Documentation:  http://galaxyproject.org/cloud"
"""

landscape_sysinfo_template = """#!/bin/sh
echo
echo -n "  System information as of "
/bin/date
echo
/usr/bin/landscape-sysinfo
"""

xvfb_init_template = """#!/bin/sh

### BEGIN INIT INFO
# Provides:        xvfb
# Required-Start:  $syslog
# Required-Stop:   $syslog
# Default-Start:   2 3 4 5
# Default-Stop:    0 1 6
# Short-Description: Start Xvfb daemon
### END INIT INFO

PATH=/sbin:/bin:/usr/sbin:/usr/bin

. /lib/lsb/init-functions

NAME=xvfb
DAEMON=/usr/bin/Xvfb
PIDFILE=/var/run/Xvfb.pid

test -x $DAEMON || exit 5

if [ -r /etc/default/$NAME ]; then
	. /etc/default/$NAME
fi

case $1 in
	start)
		log_daemon_msg "Starting Virtual Framebuffer" "Xvfb"
  		start-stop-daemon --start --quiet --background --make-pidfile --pidfile $PIDFILE --startas $DAEMON -- $XVFB_OPTS
		status=$?
		log_end_msg $status
  		;;
	stop)
		log_daemon_msg "Stopping Virtual Framebuffer" "Xvfb"
  		start-stop-daemon --stop --quiet --pidfile $PIDFILE
		log_end_msg $?
		rm -f $PIDFILE
  		;;
	restart|force-reload)
		$0 stop && sleep 2 && $0 start
  		;;
	try-restart)
		if $0 status >/dev/null; then
			$0 restart
		else
			exit 0
		fi
		;;
	reload)
		exit 3
		;;
	status)
		pidofproc -p $PIDFILE $DAEMON >/dev/null
		status=$?
		if [ $status -eq 0 ]; then
			log_success_msg "Xvfb server is running."
		else
			log_failure_msg "Xvfb server is not running."
		fi
		exit $status
		;;
	*)
		echo "Usage: $0 {start|stop|restart|try-restart|force-reload|status}"
		exit 2
		;;
esac
"""

r_packages_template = """
r <- getOption("repos");
r["CRAN"] <- "http://watson.nci.nih.gov/cran_mirror";
options(repos=r);
install.packages( c( "DBI", "RColorBrewer", "RCurl", "RSQLite", "XML", "biglm",
  "bitops", "digest", "ggplot2", "graph", "hexbin", "hwriter", "kernlab",
  "latticeExtra", "leaps", "pamr", "plyr", "proto", "qvalue", "reshape",
  "statmod", "xtable", "yacca" ), dependencies = TRUE);
source("http://bioconductor.org/biocLite.R");
biocLite( c( "AnnotationDbi", "ArrayExpress", "ArrayTools", "Biobase",
  "Biostrings", "DynDoc", "GEOquery", "GGBase", "GGtools", "GSEABase",
  "IRanges", "affy", "affyPLM", "affyQCReport", "affydata", "affyio",
  "annaffy", "annotate", "arrayQualityMetrics", "beadarray", "biomaRt",
  "gcrma", "genefilter", "geneplotter", "globaltest", "hgu95av2.db", "limma",
  "lumi", "makecdfenv", "marray", "preprocessCore", "ShortRead", "siggenes",
  "simpleaffy", "snpMatrix", "vsn" ) );
"""

xvfb_default_template = """XVFB_OPTS=":42 -auth /var/lib/xvfb/auth -ac -nolisten tcp -shmem -screen 0 800x600x24"\n"""
 
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

def configure_MI(do_rebundle=False):
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
    time_end = dt.datetime.utcnow()
    print "Duration of machine configuration: %s" % str(time_end-time_start)
    if do_rebundle == 'do_rebundle':
        do_rebundle = True
        reboot_if_needed = True
    else:
        do_rebundle = False
        reboot_if_needed = False
    if do_rebundle or confirm("Would you like to bundle this instance into a new machine image?"):
        rebundle(reboot_if_needed)

# == system

def _update_system():
    """Runs standard system update"""
    _setup_sources()
    sudo('apt-get -y update')
    run('export DEBIAN_FRONTEND=noninteractive; sudo -E apt-get upgrade -y --force-yes') # Ensure a completely noninteractive upgrade
    sudo('apt-get -y dist-upgrade')

def _setup_sources():
    """Add sources for retrieving library packages."""
    for source in env.std_sources:
        if not contains(source, env.sources_file):
            append(source, env.sources_file, use_sudo=True)

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
                'python-rpy',
                'openjdk-6-jdk',
                'postgresql-server-dev-8.4', # required for compiling ProFTPd (must match installed PostgreSQL version!)
                'r-cran-qvalue', # required by Compute q-values
                'r-bioc-hilbertvis', # required by HVIS
                'tcl-dev', # required by various R modules
                'tk-dev', # required by various R modules
                'imagemagick', # required by RGalaxy
                'pdfjam', # required by RGalaxy
                'python-scipy', # required by RGalaxy
                'libsparsehash-dev', # Pull from outside (e.g., yaml file)?
                'xvfb' ] # required by R's pdf() output
    for package in packages:
        sudo("apt-get -y --force-yes install %s" % package)

# == users

def _setup_users():
    _add_user('galaxy', '1001') # Must specify uid for 'galaxy' user because of the configuration for proFTPd
    _add_user('sgeadmin')
    _add_user('postgres')

def _add_user(username, uid=None):
    """ Add user with username to the system """
    if not contains("%s:" % username, '/etc/passwd'):
        print(yellow("System user '%s' not found; adding it now." % username))
        if uid:
            sudo('useradd -d /home/%s --create-home --shell /bin/bash -c"Galaxy-required user" --uid %s --user-group %s' % (username, uid, username))
        else:
            sudo('useradd -d /home/%s --create-home --shell /bin/bash -c"Galaxy-required user" --user-group %s' % (username, username))
        print(green("Added system user '%s'" % username))

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
    append("export DISPLAY=:42", '/etc/bash.bashrc', use_sudo=True)
    # Install required programs
    _get_sge()
    _install_nginx()
    # _install_postgresql()
    _configure_postgresql()
    _install_setuptools()
    _install_proftpd()
    _install_samtools()
    _install_openmpi()
    _install_r_packages()

def _get_sge():
    url = "%s/ge62u5_lx24-amd64.tar.gz" % CDN_ROOT_URL
    install_dir = env.install_dir
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            sudo("chown %s %s" % (env.user, install_dir))
            run("tar -C %s -xvzf %s" % (install_dir, os.path.split(url)[1]))
            print(green("----- SGE downloaded and extracted to '%s' -----" % install_dir))

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
            print(green("----- nginx upload module downloaded and extracted to '%s' -----" % install_dir))
    
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
    url = os.path.join(REPO_ROOT_URL, nginx_conf_file)
    remote_conf_dir = os.path.join(install_dir, "conf")
    with cd(remote_conf_dir):
        sudo("wget --output-document=%s/%s %s" % (remote_conf_dir, nginx_conf_file, url))
    
    nginx_errdoc_file = 'nginx_errdoc.tar.gz'
    url = os.path.join(REPO_ROOT_URL, nginx_errdoc_file)
    remote_errdoc_dir = os.path.join(install_dir, "html")
    with cd(remote_errdoc_dir):
        sudo("wget --output-document=%s/%s %s" % (remote_errdoc_dir, nginx_errdoc_file, url))
        sudo('tar xvzf %s' % nginx_errdoc_file)
    print(green("----- nginx installed and configured -----"))

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
                print(green("----- PostgreSQL installed -----"))

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
    print(green("----- PostgreSQL configured -----"))

@_if_not_installed("easy_install")
def _install_setuptools():
    version = "0.6c11"
    python_version = "2.6"
    url = "http://pypi.python.org/packages/%s/s/setuptools/setuptools-%s-py%s.egg#md5=bfa92100bd772d5a213eedd356d64086" % (python_version, version, python_version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            sudo("sh %s" % os.path.split(url)[1].split('#')[0])
            print(green("----- setuptools installed -----"))

def _install_proftpd():
    version = "1.3.3d"
    postgres_ver = "8.4"
    url = "ftp://mirrors.ibiblio.org/proftpd/distrib/source/proftpd-%s.tar.gz" % version
    install_dir = os.path.join(env.install_dir, 'proftpd')
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            with settings(hide('stdout')):
                run("tar xvzf %s" % os.path.split(url)[1])
            with cd("proftpd-%s" % version):
                run("CFLAGS='-I/usr/include/postgresql' ./configure --prefix=%s --disable-auth-file --disable-ncurses --disable-ident --disable-shadow --enable-openssl --with-modules=mod_sql:mod_sql_postgres:mod_sql_passwd --with-libraries=/usr/lib/postgres/%s/lib" % (install_dir, postgres_ver))
                sudo("make")
                sudo("make install")
                sudo("make clean")
                # Get init.d startup script
                initd_script = 'proftpd'
                initd_url = os.path.join(REPO_ROOT_URL, 'conf_files', initd_script)
                sudo("wget --output-document=%s %s" % (os.path.join('/etc/init.d', initd_script), initd_url))
                sudo("chmod 755 %s" % os.path.join('/etc/init.d', initd_script))
                # Get configuration files
                proftpd_conf_file = 'proftpd.conf'
                welcome_msg_file = 'welcome_msg.txt'
                conf_url = os.path.join(REPO_ROOT_URL, 'conf_files', proftpd_conf_file)
                welcome_url = os.path.join(REPO_ROOT_URL, 'conf_files', welcome_msg_file)
                remote_conf_dir = os.path.join(install_dir, "etc")
                sudo("wget --output-document=%s %s" % (os.path.join(remote_conf_dir, proftpd_conf_file), conf_url))
                sudo("wget --output-document=%s %s" % (os.path.join(remote_conf_dir, welcome_msg_file), welcome_url))
                sudo("cd %s; stow proftpd" % env.install_dir)
                print(green("----- ProFTPd %s installed to %s -----" % (version, install_dir)))

def _install_samtools():
    version = "0.1.12"
    vext = "a"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/samtools/samtools/%s/" \
            "samtools-%s%s.tar.bz2" % (version, version, vext)
    install_dir = "/usr/bin"
    install_cmd = sudo
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            with cd("samtools-%s%s" % (version, vext)):
                run("sed -i.bak -r -e 's/-lcurses/-lncurses/g' Makefile")
                #sed("Makefile", "-lcurses", "-lncurses")
                run("make")
                for install in ["samtools", "misc/maq2sam-long"]:
                    install_cmd("mv -f %s %s" % (install, install_dir))
                print "----- SAMtools %s installed to %s -----" % (version, install_dir)

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
                print(green("----- OpenMPI %s installed to %s -----" % (version, install_dir)))

def _install_r_packages():
    f = tempfile.NamedTemporaryFile()
    f.write(r_packages_template)
    f.flush()
    with _make_tmp_dir() as work_dir:
        put(f.name, os.path.join(work_dir, 'install_packages.r'))
        with cd(work_dir):
            sudo("R --vanilla --slave < install_packages.r")
    f.close()
    print(green("----- R packages installed -----"))

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
            version = run('python -c"import boto; print boto.__version__"')
            print(green("----- boto %s installed -----" % version))

# == environment

def _configure_environment():
    _configure_ec2_autorun()
    _configure_sge()
    _configure_galaxy_env()
    _configure_nfs()
    _configure_bash()
    _configure_xvfb()

def _configure_ec2_autorun():
    url = os.path.join(REPO_ROOT_URL, "ec2autorun.py")
    sudo("wget --output-document=%s/ec2autorun.py %s" % (env.install_dir, url))
    # Create upstart configuration file for boot-time script
    cloudman_boot_file = 'cloudman.conf'
    with open( cloudman_boot_file, 'w' ) as f:
        print >> f, cm_upstart % env.install_dir
    put(cloudman_boot_file, '/tmp/%s' % cloudman_boot_file) # Because of permissions issue
    sudo("mv /tmp/%s /etc/init/%s; chown root:root /etc/init/%s" % (cloudman_boot_file, cloudman_boot_file, cloudman_boot_file))
    os.remove(cloudman_boot_file)
    print(green("----- ec2_autorun added to upstart -----"))
    
    # Create upstart configuration file for RabbitMQ
    rabbitmq_server_conf = 'rabbitmq-server.conf'
    with open( rabbitmq_server_conf, 'w' ) as f:
        print >> f, rabitmq_upstart #% env.install_dir
    put(rabbitmq_server_conf, '/tmp/%s' % rabbitmq_server_conf) # Because of permissions issue
    sudo("mv /tmp/%s /etc/init/%s; chown root:root /etc/init/%s" % (rabbitmq_server_conf, rabbitmq_server_conf, rabbitmq_server_conf))
    os.remove(rabbitmq_server_conf)
    # Stop the init.d script
    sudo('/usr/sbin/update-rc.d -f rabbitmq-server remove')
    print(green("----- RabbitMQ added to upstart -----"))

def _configure_sge():
    """This method only sets up the environment for SGE w/o actually setting up SGE"""
    sge_root = '/opt/sge'
    if not exists(sge_root):
        sudo("mkdir -p %s" % sge_root)
        sudo("chown sgeadmin:sgeadmin %s" % sge_root)

def _configure_galaxy_env():
    # Edit the galaxy user .bash_profile & .bashrc
    if exists('/home/galaxy/.bash_profile'):
        append('export TEMP=/mnt/galaxyData/tmp', '/home/galaxy/.bash_profile', use_sudo=True)
        sudo('chown galaxy:galaxy /home/galaxy/.bash_profile')
    if exists('/home/galaxy/.bashrc'):
        append('export TEMP=/mnt/galaxyData/tmp', '/home/galaxy/.bashrc', use_sudo=True)
        sudo('chown galaxy:galaxy /home/galaxy/.bashrc')
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
    # Customize instance login welcome message
    welcome_msg_template_file = '10-help-text'
    with open( welcome_msg_template_file, 'w' ) as f:
        print >> f, welcome_msg_template
    put(welcome_msg_template_file, '/tmp/%s' % welcome_msg_template_file) # Because of permissions issue
    sudo("mv /tmp/%s /etc/update-motd.d/%s; chown root:root /etc/update-motd.d/%s" % (welcome_msg_template_file, welcome_msg_template_file, welcome_msg_template_file))
    sudo("chmod +x /etc/update-motd.d/%s" % welcome_msg_template_file)
    os.remove(welcome_msg_template_file)
    
    landscape_sysinfo_template
    landscape_sysinfo_template_file = '50-landscape-sysinfo'
    with open( landscape_sysinfo_template_file, 'w' ) as f:
        print >> f, landscape_sysinfo_template
    put(landscape_sysinfo_template_file, '/tmp/%s' % landscape_sysinfo_template_file) # Because of permissions issue
    sudo("mv /tmp/%s /etc/update-motd.d/%s; chown root:root /etc/update-motd.d/%s" % (landscape_sysinfo_template_file, landscape_sysinfo_template_file, landscape_sysinfo_template_file))
    sudo("chmod +x /etc/update-motd.d/%s" % landscape_sysinfo_template_file)
    os.remove(landscape_sysinfo_template_file)
    
    sudo('if [ -f /etc/update-motd.d/51_update_motd ]; then rm -f /etc/update-motd.d/51_update_motd; fi')
    
    append(['alias lt=\"ls -ltr\"', 'alias mroe=more'], '/etc/bash.bashrc', use_sudo=True)

def _configure_xvfb():
    """Configure the virtual X framebuffer which is necessary for a couple tools."""
    xvfb_init_file = 'xvfb_init'
    with open( xvfb_init_file, 'w' ) as f:
        print >> f, xvfb_init_template
    put(xvfb_init_file, '/tmp/%s' % xvfb_init_file)
    sudo("mv /tmp/%s /etc/init.d/xvfb; chown root:root /etc/init.d/xvfb; chmod 0755 /etc/init.d/xvfb" % xvfb_init_file)
    xvfb_default_file = 'xvfb_default'
    with open( xvfb_default_file, 'w' ) as f:
        print >> f, xvfb_default_template
    put(xvfb_default_file, '/tmp/%s' % xvfb_default_file)
    sudo("mv /tmp/%s /etc/default/xvfb; chown root:root /etc/default/xvfb" % xvfb_default_file)
    sudo("ln -s /etc/init.d/xvfb /etc/rc0.d/K01xvfb")
    sudo("ln -s /etc/init.d/xvfb /etc/rc1.d/K01xvfb")
    sudo("ln -s /etc/init.d/xvfb /etc/rc2.d/S99xvfb")
    sudo("ln -s /etc/init.d/xvfb /etc/rc3.d/S99xvfb")
    sudo("ln -s /etc/init.d/xvfb /etc/rc4.d/S99xvfb")
    sudo("ln -s /etc/init.d/xvfb /etc/rc5.d/S99xvfb")
    sudo("ln -s /etc/init.d/xvfb /etc/rc6.d/K01xvfb")
    sudo("mkdir /var/lib/xvfb; chown root:root /var/lib/xvfb; chmod 0755 /var/lib/xvfb")
    print(green("----- configured xvfb -----"))

# == Machine image rebundling code
def rebundle(reboot_if_needed=False):
    """
    Rebundles the EC2 instance that is passed as the -H parameter
    This script handles all aspects of the rebundling process and is (almost) fully automated.
    Two things should be edited and provided before invoking it: AWS account information 
    and the desired size of the root volume for the new instance.  
     
    :rtype: bool
    :return: If instance was successfully rebundled and an AMI ID was received,
             return True.
             False, otherwise.
    """
    time_start = dt.datetime.utcnow()
    print "Rebundling instance '%s'. Start time: %s" % (env.hosts[0], time_start)
    if boto:
        # Select appropriate region:
        availability_zone = run("curl --silent http://169.254.169.254/latest/meta-data/placement/availability-zone")
        instance_region = availability_zone[:-1] # Truncate zone letter to get region name
        ec2_conn = _get_ec2_conn(instance_region)
        vol_size = 15 # This will be the size (in GB) of the root partition of the new image
        
        # hostname = env.hosts[0] # -H flag to fab command sets this variable so get only 1st hostname
        instance_id = run("curl --silent http://169.254.169.254/latest/meta-data/instance-id")
        
        # Handle reboot if required
        if _reboot(ec2_conn, instance_id, reboot_if_needed):
            return False # Indicates that rebundling was not completed and should be restarted
        
        _clean() # Clean up the environment before rebundling
        image_id = None
        kernel_id = run("curl --silent http://169.254.169.254/latest/meta-data/kernel-id")
        if instance_id and availability_zone and kernel_id:
            print "Rebundling instance with ID '%s' in region '%s'" % (instance_id, ec2_conn.region.name)
            try:
                # Need 2 volumes - one for image (rsync) and the other for the snapshot (see instance-to-ebs-ami.sh)
                vol = ec2_conn.create_volume(vol_size, availability_zone)
                vol2 = ec2_conn.create_volume(vol_size, availability_zone)
                # TODO: wait until it becomes 'available'
                print "Created 2 new volumes of size '%s' with IDs '%s' and '%s'" % (vol_size, vol.id, vol2.id)
            except EC2ResponseError, e:
                print(red("Error creating volume: %s" % e))
                return False
            
            if vol:
                try:
                    # Attach newly created volumes to the instance
                    dev_id = '/dev/sdh'
                    if not _attach(ec2_conn, instance_id, vol.id, dev_id):
                        print(red("Error attaching volume '%s' to the instance. Aborting." % vol.id))
                        return False
                    dev_id = '/dev/sdj'
                    if not _attach(ec2_conn, instance_id, vol2.id, dev_id):
                        print(red("Error attaching volume '%s' to the instance. Aborting." % vol2.id))
                        return False
                    # Move the file system onto the new volume (with a help of a script)
                    url = os.path.join(REPO_ROOT_URL, "instance-to-ebs-ami.sh")
                    # with contextlib.nested(cd('/tmp'), settings(hide('stdout', 'stderr'))):
                    with cd('/tmp'):
                        if exists('/tmp/'+os.path.split(url)[1]):
                            sudo('rm /tmp/'+os.path.split(url)[1])
                        sudo('wget %s' % url)
                        sudo('chmod u+x /tmp/%s' % os.path.split(url)[1])
                        sudo('./%s' % os.path.split(url)[1])
                    # Detach the new volume
                    _detach(ec2_conn, instance_id, vol.id)
                    _detach(ec2_conn, instance_id, vol2.id)
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
                    ebs.delete_on_termination = True
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
                    print "Deleting the volume (%s) used for rsync only" % vol2.id
                    ec2_conn.delete_volume(vol2.id)
                    print(green("--------------------------"))
                    print(green("Finished creating new machine image. Image ID: '%s'" % (image_id)))
                    print(green("--------------------------"))
                    answer = confirm("Would you like to make this machine image public?", default=False)
                    if image_id and answer:
                        ec2_conn.modify_image_attribute(image_id, attribute='launchPermission', operation='add', groups=['all'])
                except EC2ResponseError, e:
                    print(red("Error creating image: %s" % e))
                    return False
            else:
                print(red("Error creating new volume"))
                return False
        else:
            print(red("Error retrieving instance availability zone"))
            return False            
    else:
        print(red("Python boto library not available. Aborting."))
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
    if (force or exists("/var/run/reboot-required")) and instance_id:
        answer = False
        if not force:
            answer = confirm("Before rebundling, instance '%s' needs to be rebooted. Reboot instance?" % instance_id)
        if force or answer:
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
                        print "Checking ssh connectivity to instance '%s'" % env.hosts[0]
                        ssh = local('ssh -o StrictHostKeyChecking=no -i %s %s@%s "exit"' % (env.key_filename[0], env.user, env.hosts[0]))
                    if ssh.return_code == 0:
                        print(green("\n--------------------------"))
                        print(green("Machine '%s' is alive" % env.hosts[0]))
                        print(green("This script will exit now. Invoke it again while passing method name 'rebundle' as the last argument to the fab script."))
                        print(green("--------------------------\n"))
                        return True
                    else:
                        print "Still waiting..."
                        time.sleep(3)
                    if i == 29:
                        print(red("Machine '%s' did not respond for while now, aborting" % env.hosts[0]))
                        return True
            except EC2ResponseError, e:
                print(red("Error rebooting instance '%s' with IP '%s': %s" % (instance_id, env.hosts[0], e)))
                return False
            except Exception, e:
                print(red("Error rebooting instance '%s' with IP '%s': %s" % (instance_id, env.hosts[0], e)))
                print(red("Try running this script again with 'rebundle' as the last argument."))
                return False
        else:
            print(red("Cannot rebundle without instance reboot. Aborting rebundling."))
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
            print(red("Volume '%s' FAILED to attach to instance '%s' as device '%s'. Aborting." % ( volume_id, instance_id, device )))
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
        print(red("Detaching volume '%s' from instance '%s' failed. Exception: %s" % ( volume_id, instance_id, e )))
        return False
    
    for counter in range( 30 ):
        print "Volume '%s' status '%s'" % ( volume_id, volumestatus )
        if volumestatus == 'available':
            print "Volume '%s' successfully detached from instance '%s'." % ( volume_id, instance_id )
            break
        if counter == 29:
            print(red("Volume '%s' FAILED to detach to instance '%s'." % ( volume_id, instance_id )))
        time.sleep( 3 )
        volumes = ec2_conn.get_all_volumes( [volume_id] )
        volumestatus = volumes[0].status

def _create_snapshot(ec2_conn, volume_id, description=None):
    """
    Create a snapshot of the EBS volume with the provided volume_id. 
    Wait until the snapshot process is complete (note that this may take quite a while)
    """
    snap_start_time = dt.datetime.utcnow()
    print "Initiating snapshot of EBS volume '%s' in region '%s' at '%s'" % (volume_id, ec2_conn.region.name, snap_start_time)
    snapshot = ec2_conn.create_snapshot(volume_id, description=description)
    if snapshot: 
        while snapshot.status != 'completed':
            print "Snapshot '%s' progress: '%s'; status: '%s'; duration: %s" % (snapshot.id, snapshot.progress, snapshot.status, (dt.datetime.utcnow()-snap_start_time))
            time.sleep(10)
            snapshot.update()
        print(green("Creation of snapshot for volume '%s' completed at '%s' (duration %s): '%s'" % (volume_id, dt.datetime.utcnow(), (dt.datetime.utcnow()-snap_start_time), snapshot)))
        return snapshot.id
    else:
        print(red("Could not create snapshot from volume with ID '%s'" % volume_id))
        return False

def _clean_rabbitmq_env():
    """
    RabbitMQ fails to start if its database is embedded into the image because it saves the current
    IP address or host name so delete it now. When starting up, RabbitMQ will recreate that directory.
    """
    print "Cleaning RabbitMQ environment"
    with settings(warn_only=True):
        sudo('/etc/init.d/rabbitmq-server stop') # If upstart script is used, upstart will restart rabbitmq upon stop
    sudo('initctl reload-configuration')
    with settings(warn_only=True):
        sudo('stop rabbitmq-server')
    if exists('/var/lib/rabbitmq/mnesia'):
        sudo('rm -rf /var/lib/rabbitmq/mnesia')

def _clean():
    """Clean up the image before rebundling"""
    # Make sure RabbitMQ environment is clean
    _clean_rabbitmq_env()
    # Stop Apache from starting automatically at boot (it conflicts with Galaxy's nginx)
    sudo('/usr/sbin/update-rc.d -f apache2 remove')
    # Cleanup some of the logging files that might get bundled into the image
    for cf in ['%s/ec2autorun.py.log' % env.install_dir, '/var/crash/*', '/var/log/firstboot.done', '$HOME/.nx_setup_done']:
        if exists(cf):
            sudo('rm -f %s' % cf)

def _get_ec2_conn(instance_region='us-east-1'):
    regions = boto.ec2.regions()
    print "Found regions: %s; trying to match to instance region: %s" % (regions, instance_region)
    region = None
    for r in regions:
        if instance_region in r.name:
            region = r
            break
    if not region:
        print(red("ERROR discovering a region; try running this script again using 'rebundle' as the last argument."))
        return None
    try:
        ec2_conn = EC2Connection(region=region)
        return ec2_conn
    except EC2ResponseError, e:
        print(red("ERROR getting EC2 connections: %s" % e))
        return None
