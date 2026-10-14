Put together a pytest to test  the status of a built image via ci and provide usable data if it encounters an error (ex no file found)


Things to remember:
remember to actually confirm all the things you want added to a commit are in said commit before you send it, otherwise you look like a dolt. Make sure things are save/autosave is on, or you'll ship an empty file.

If you're building a check/test, make sure to actually run the damn thing. Committed on everything without adding the script check to the main-merge gate, which would mean a PR could merge without actually running the script check.

Merged without testing a terraform apply manually, instead running on simulator, and immediately ran into a permissions problem. Should have resolved all permissions issues now instead of delaying to further in the curriculum.
# Creating inside a parent: AWS also authorizes CreateSubnet and CreateRouteTable against the
  # parent VPC, and CreateNatGateway against its subnet and Elastic IP, each ARN on its own. The
  # request's tags only exist for the new resource, so Ec2CreateTagged can't pass for a parent:
  # the parent must already be this environment's. That also means no creating inside a VPC
  # this role doesn't own (was risk R12).
