mi-deployment project is a set of scripts that orchestrate and automate the 
process of customizing a machine image (MI). Its primary applicability is for 
the Galaxy CloudMan project (http://userwww.service.emory.edu/~eafgan/projects.html)
where it sets up the necessary environment. The project is currently used to create
Galaxy deployments on the Amazon Elastic Compute Cloud (EC2) as well as the Galaxy
VM (http://usegalaxy.org/vm). The provided set of scripts should also be applicable 
in other environments, namely a local cloud deployment, a single server setup, or
for deploying other applications in a similar environment.

******************* Overview *******************
NOTE: In order to use the scripts provided within the mi-deployment project, 
Python Fabric v1.0* (http://docs.fabfile.org/) and boto (http://github.com/boto/boto)
need to be available on the system from where mi-deployments scripts are run. 

There are two basic scripts that can be run as part of mi-deployment (the rest of 
the scripts in the repository are used automatically by these two main scripts):
  - mi_fabfile.py: this script sets up the machine image and automates the 
    process of image rebundling
  - tools_fabfile.py: this script installs a range of bioinformatics tools 
    exposed by the Galaxy application
    
~~~~~~~~~~~~~~~~~~ mi_fabfile.py ~~~~~~~~~~~~~~~~~~
  When run, the mi_fabfile.py script performs the following set of operations:
   - update the system
   - install packages required for running Galaxy CloudMan
   - setup additional system users
   - install required programs for running Galaxy CloudMan
   - install required python libraries for running Galaxy CloudMan
   - customize and configure the system environment
  At the completion of the machine image customization, the script offers an 
  option to rebundle and register the new image with the cloud provider.
  
  USAGE:
  Before running this script, in the context of Amazon EC2, you will either have
  to define the following two environment variables or edit the script and 
  specify your AWS account keys in the code (in rebundle() method):
  export AWS_ACCESS_KEY_ID=<Your AWS Access Key ID>
  export AWS_SECRET_ACCESS_KEY=<Your AWS Secret Access Key>
  
  In order to run the script, one should:
   1. Start a machine instance based on a compatible AMI
   2. Run the script, specifying the instance as one of the arguments
  The mi_deployment and Galaxy CloudMan projects target Ubuntu 10.04 operating
  system; however, any comparable derivative of the given OS should result in
  a compatible AMI. In the specific case of the Galaxy CloudMan, in order to 
  deliver a broad set of bioinformatics tools to our users, we use a Cloud 
  BioLinux AMI (http://cloudbiolinux.com/) and build on top of it.
  For the rebundling process to work, the instance used must be based on an EBS AMI.
  
  Specifically, once an instance of the given AMI is running, from the local 
  machine, run the following command:
  fab -f mi_fabfile.py -i <full_path_to_private_key_file> -H <instance_public_dns> configure_MI
  
  NOTE: When the script finishes with the system configuration, it is going to 
  prompt whether a new AMI should be created from the given instance. If the AMI 
  rebundling is to take place, depending on the amount of system updates, it is 
  very likely that the remote instance will need to be rebooted. If that is the 
  case, the mi_fabfile.py will automatically reboot the instance and exit. You 
  should then run the script again, using 'rebundle' as the last argument, like so:
  fab -f mi_fabfile.py -H <instance_public_dns> -i <full_path_to_private_key_file> rebundle
  
  The script will proceed with instance rebundling, prompting you for couple 
  more options. Once done, a new AMI will have been created under your account.
 
~~~~~~~~~~~~~~~~~~ tools_fabfile.py ~~~~~~~~~~~~~~~~~~
  When run, the tools_fablile.py script installs a set of (bioinformatics) tools. 
  These tools and their installation properties are primarily intended for use 
  with the Galaxy application but can easily be adopted for other uses as well. 
  The list of tools being installed is available under method '_install_tools'.
  
  This script expects an environment to be configured before it is run. This
  environment can be specified in a method corresponding to a given machine and 
  thus the script can be reusable in a variety of scenarios. In the most generic
  case, and for use with Amazon EC2, the method 'amazon_ec2' sets up a sample 
  environment. Specifically, all this entails is availability of a file system 
  at the location specified in the environment setup method.
  
  For example, for use with Amazon EC2, the act of using this script would assume
  starting an EC2 instance, attaching an EBS volume to it, creating a file system
  on the newly created volume, and mounting it at paths specified in the 'amazon_ec2' 
  method (e.g., /mnt/galaxyTools/).
  
  Once the environment is ready, the script can be invoked from a local machine
  using the following command:
  fab -f tools_fabfile.py -i <full_path_to_private_key_file> -H <instance_public_dns> install_tools
  
  As a reminder, for the Amazon EC2 case, unless all of the tools are being 
  installed on the root volume/file system and a new AMI will be (manually) created
  in order to persist the changes, you will need to unmount the file system 
  where the tools were installed, detach the given EBS volume and create a snapshot
  of it. Then, the next time you plan on using the tools or the volume, you can create 
  a new volume based on the created snapshot, attach it to a new instance, 
  and mount it (at the same location that was used for installing the tools).
