# Ansible: A hands-on configuration-management track

The main curriculum provisions infrastructure with Terraform and reconciles clusters with Flux. Neither of those is how you configure a machine you already have. That job (install these packages, write these config files, make sure this service is running, and do it the same way every time) is configuration management, and Ansible is the tool most teams reach for. This track teaches it by having you configure two machines you already own: a Linux host (your WSL environment) and your Windows desktop.

This is a side track, not part of the Acme capstone. You can do it any time after Foundations. It pairs naturally with Module 1 (you'll understand packages, services, and the filesystem better after doing it) and with Module 4 (you'll see the same desired-state idea that Kubernetes uses, pushed instead of pulled). Two modules, roughly a week each.

## Prerequisites

You need the environment you built in Foundations Week 0a:

- WSL2 with Ubuntu, working from a terminal
- Docker Desktop with the WSL2 backend, and `docker run hello-world` succeeds from inside WSL
- Git, and the fork-and-PR workflow you've been using for the journal

One invariant to fix in your head before you install anything: **the Ansible control node runs on Linux.** You run Ansible from inside WSL. Ansible does not run as a control node on Windows, and that is not a limitation you're going to work around. Your Windows desktop in Module A2 is a *managed node* (a thing Ansible configures), reached across a boundary, not the thing Ansible runs on. If you ever find a tutorial telling you to `pip install ansible` in PowerShell, close it.

Each module ends two ways, same as Foundations: a `journal/` entry pushed to your repo, and working code committed under a new top-level `ansible/` directory:

```
ansible/
├── inventory/      # which hosts, and how to reach them
├── roles/          # reusable units of configuration
└── playbooks/      # what to run against which hosts
```

You write all of that yourself. This track does not hand you starter files. You'll scaffold the skeletons with `ansible-galaxy` and `molecule`, which is itself a skill worth having.

---

## Shared setup: the Ansible toolchain

Do this once. Both modules use it.

Ansible is Python, and the fastest way to make a mess is to `sudo pip install` things into your system Python until an apt upgrade breaks them. Don't. Isolate the tools. You have two reasonable options:

- **pipx** (recommended for the command-line tools). pipx installs each Python application into its own virtualenv and puts the commands on your `PATH`, so `ansible`, `molecule`, and `ansible-lint` don't fight each other or your system Python. `sudo apt install pipx`, then `pipx ensurepath`, then open a new shell.
- **A project virtualenv** (`python3 -m venv .venv` in your `ansible/` directory, then `source .venv/bin/activate`). More explicit, and closer to how a real project pins its tools. If you go this way, add `.venv/` to `.gitignore`.

Pick one and know which you picked. Mixing them is how you end up with two `ansible` binaries and a confusing afternoon.

What to install:

- **`ansible`** vs **`ansible-core`.** `ansible-core` is the engine plus a small set of built-in modules. The `ansible` package is `ansible-core` plus a large bundle of community collections. Install `ansible` to start; you get the Windows collections you'll need in Module A2 without hunting for them. Later, when you care about exactly what's installed, you'll learn to pin `ansible-core` and declare collections explicitly in a `requirements.yml`. Note the version you installed (`ansible --version`) and write it in your journal.
- **`molecule`** plus the Docker driver. Molecule is the test harness for roles. Since Molecule 6 the drivers are shipped separately, so you install the Docker driver as its own package: `molecule-plugins[docker]`. If you install plain `molecule` and get an error about an unknown `docker` driver, this is why. Molecule needs the Docker you set up in Foundations to be reachable from WSL.
- **`ansible-lint`** and **`yamllint`.** The linters. `ansible-lint` catches the mistakes that still run but shouldn't (using a shell command where a module exists, non-idempotent patterns, deprecated syntax). `yamllint` catches the YAML that doesn't parse the way you think it does. Run them before you commit, the same way you'd run `terraform fmt`.

Confirm the stack works before you write anything real:

```
ansible --version
molecule --version
docker run --rm hello-world
```

### Scaffolding, not copying

Ansible has strong conventions about directory layout, and the tools will generate the skeleton for you. Learn to lean on them:

- `ansible-galaxy role init <name>` creates a role directory with the standard `tasks/`, `handlers/`, `defaults/`, `templates/`, `meta/` layout. The layout is not arbitrary; Ansible loads files by convention, so `tasks/main.yml` runs automatically and `defaults/main.yml` is found without you wiring anything up. Build the habit of knowing what each directory is for.
- `molecule init scenario` adds a Molecule test scenario to an existing role. It writes a `molecule/default/` directory with the config that says "spin up this container, run the role against it, then check it."

Exact command syntax shifts between versions, so when something doesn't match what you read here, trust the official docs over this page:

- Ansible getting started and user guide: https://docs.ansible.com/ansible/latest/getting_started/index.html
- Molecule docs: https://ansible.readthedocs.io/projects/molecule/
- ansible-lint docs: https://ansible.readthedocs.io/projects/lint/

---

## Module A1: Ansible fundamentals on Linux

The goal this week is a single, well-structured, idempotent role that you can prove works by running it against a throwaway container, and that you then run against your own WSL host.

### Concepts

- **Inventory.** The list of hosts Ansible manages and how to reach them. Start with a static INI or YAML file. Understand groups (you address `all`, or a group like `webservers`, or a single host).
- **Modules, tasks, plays, playbooks.** A *module* is a unit of work (`ansible.builtin.apt`, `ansible.builtin.copy`, `ansible.builtin.service`). A *task* is one call to a module with arguments. A *play* maps a list of tasks onto a group of hosts. A *playbook* is a file of one or more plays. Keep these words straight; the error messages assume you know which is which.
- **Roles and role layout.** A role is a reusable bundle of tasks, handlers, templates, and default variables, in the standard directory layout `ansible-galaxy role init` gives you. Roles are how you avoid 800-line playbooks. The rule from Module 2 applies here too: if you wouldn't write a 2,000-line function, don't write a 2,000-line role.
- **Idempotency.** Running the role once and running it ten times should leave the machine in the same state, and every run after the first should report zero changes. This is the whole game. A task that reports "changed" every single run is a bug, even when the end state is correct, because you can no longer tell a real change from noise.
- **Facts.** Before it runs your tasks, Ansible gathers *facts* about the target (OS, IP addresses, installed Python, mounted filesystems). Facts are how a role adapts: "if this is Debian, use apt." Run `ansible <host> -m ansible.builtin.setup` once and read what comes back.
- **Handlers.** A task that only runs when something notified it, and only once at the end of the play. The canonical use: a task that writes a config file notifies a handler that restarts the service, so the service restarts only when the config actually changed. This is idempotency applied to side effects.
- **Variables and precedence.** Variables can come from `defaults/`, inventory, the command line, and several places in between, and they override each other in a defined order. You don't need to memorize the full precedence table, but you do need to know that `defaults/main.yml` is the lowest priority (the role's sensible default, meant to be overridden) and that `-e` on the command line wins over almost everything.
- **Templates (Jinja2).** A config file with `{{ variable }}` placeholders, rendered per host with the `ansible.builtin.template` module. This is how one role configures ten machines that each need a slightly different config.
- **`ansible-vault`.** Encrypts secrets (a password, an API key) so you can commit them to Git without committing them in the clear. You won't need much of it this week, but know it exists so you never commit a plaintext password "just for now."
- **Check mode and `--diff`.** `--check` does a dry run: Ansible reports what it *would* change without changing it. `--diff` shows the before/after of files it would touch. Together they're your `terraform plan`. Use them before you run anything against a host you care about.
- **Tags.** Label tasks so you can run a subset (`--tags config`) instead of the whole playbook. Useful once a role grows.

### Systems-thinking lens

You've already met this pattern. In Module 4, Kubernetes reconciles the cluster toward a desired state in a continuous loop. In Module 8, Flux does the same for deployments. Ansible is the same desired-state idea with one crucial difference: it's *push-based and run-once*. Nobody is watching. The loop runs when you run it, converges the machine toward the state your role describes, and then stops.

That difference is the thing to internalize. A Kubernetes controller is a balancing feedback loop that runs forever, so drift gets corrected within a reconciliation interval whether you're watching or not. An Ansible run is a single pass of that same loop that you trigger by hand (or from CI). Between runs, the machine can drift, and nothing corrects it until the next run. That's not a flaw; it's a different point on the push/pull tradeoff from Module 8. It's why configuration management and GitOps solve overlapping problems in different ways, and why you'd reach for each.

Idempotency is what makes the single pass safe to repeat. Facts are the "observe actual state" half of the loop: Ansible looks at the machine, compares it to what the role declares, and acts only on the gap. When you write a task that reports "changed" every run, you've broken the "observe" step; you're acting without checking, which is exactly the failure mode Meadows warns about when a system acts on the goal instead of the gap.

Molecule's idempotence test makes this concrete. It runs your role twice and fails the build if the second run reports any change. It is literally asserting that the loop reaches a fixed point. When that test fails, you have a task that doesn't check actual state before acting. Find it and fix it; don't suppress the test.

### Resources

- Ansible getting started: https://docs.ansible.com/ansible/latest/getting_started/index.html
- Playbooks guide: https://docs.ansible.com/ansible/latest/playbook_guide/index.html
- Roles: https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse_roles.html
- Vault: https://docs.ansible.com/ansible/latest/vault_guide/index.html
- Molecule: https://ansible.readthedocs.io/projects/molecule/
- *Ansible for DevOps* by Jeff Geerling. The most practical book on the tool, and he keeps it current. Read the early chapters, then use it as a reference.

### Hands-on

Pick one small, real job. Good first roles: install and configure nginx with a templated `index.html` and a handler that reloads on config change; or create a user, install a short list of packages, and lay down a couple of dotfiles. Keep the scope small. A role that does one thing well teaches more than a role that does five things badly.

1. Scaffold the role with `ansible-galaxy role init`. Read every file it created and delete the ones you won't use.
2. Add your tasks to `tasks/main.yml`. Use real modules (`ansible.builtin.apt`, `ansible.builtin.copy`, `ansible.builtin.template`, `ansible.builtin.service`), not `command` or `shell`, unless there is genuinely no module for the job. Using `shell` where a module exists is the most common way to lose idempotency.
3. Add a template under `templates/` and a variable in `defaults/main.yml` that it uses. Prove the variable overrides work by passing `-e` on the command line.
4. Add a `molecule init scenario` scenario. Run `molecule converge` to apply the role to a fresh Docker container. Then run `molecule idempotence` (or the full `molecule test`) and watch it assert that a second run changes nothing. Break idempotency on purpose (add a `command:` task with no `creates:` guard), watch the test fail, then fix it.
5. Write a `verify.yml` in the scenario that asserts the end state (the package is installed, the service is running, the file contains the templated value). A role that converges is not the same as a role that did the right thing; the verify step is where you check the right thing happened.
6. Run `ansible-lint` and fix what it flags. Read *why* it flagged each one.
7. Finally, run the role against your actual WSL host with a playbook, using `--check --diff` first. Seeing it touch a real machine (yours) is the point.

### Deliverable

- Under `ansible/roles/<name>/`: the role and its Molecule scenario, committed.
- `journal/ansible-linux.md`: what the role does, the inventory you used, the output of a `molecule test` run (paste it), and one idempotency bug you hit and how you found and fixed it. Write the bug up honestly; the one you had to debug is the one you'll remember.

---

## Module A2: Ansible for your local Windows machine

This week you reach across the boundary from WSL to the Windows host it runs on, and configure Windows with the same declarative approach. Managing Windows with Ansible is a real skill (plenty of shops run Ansible against Windows fleets), and doing it against your own desktop is a safe way to learn it.

Set expectations first: this is harder than Module A1, and most of the difficulty is in the connection and the authentication, not the Ansible. Budget your time accordingly. If you get `win_ping` to return `pong`, you've done the hard 60%.

### Concepts

- **Control node vs managed node.** Unchanged from the intro, but now it bites. Ansible runs in WSL (the control node). The Windows host is the managed node. These are two different operating systems on one physical machine, and Ansible has to open a real network connection from one to the other.
- **Reaching the Windows host from WSL.** WSL2 runs in its own network namespace, so "the Windows machine this is running on" is not `localhost` by default. You find the Windows host's address from the WSL side, typically the default gateway (`ip route show default` gives you the gateway IP, which is the Windows host). On Windows 11 with WSL *mirrored networking mode* enabled, WSL and Windows share a network and you can reach the host as `localhost`. Figure out which situation you're in and record the address; this is the single most common place people get stuck.
- **Connection transport: SSH vs WinRM.** Two ways for Ansible to talk to Windows:
  - **OpenSSH on Windows** (recommended to start). Modern Windows ships an optional OpenSSH Server feature. The `ansible.windows` collection supports SSH, and if you did the Linux module, SSH is a model you already understand. You enable the OpenSSH Server optional feature, start its service, and connect much as you would to a Linux box, with `ansible_shell_type: powershell` set so Ansible knows the remote shell is PowerShell, not bash.
  - **WinRM** (the traditional transport). Windows Remote Management, Microsoft's own remote-execution protocol. It works and plenty of docs assume it, but it has more setup (listeners, authentication modes, certificates for HTTPS) and more ways to go wrong on a first attempt. Know it exists; prefer SSH for your first run.
- **The Windows collections.** Windows modules live in collections, not in `ansible-core`. The two you want are `ansible.windows` (core Windows modules: `win_ping`, `win_package`, `win_service`, `win_copy`, `win_feature`) and `community.windows` (more, including things like `win_scheduled_task`). The `ansible` bundle you installed in the shared setup already includes these. The modules are named `win_*`; a Linux `ansible.builtin.copy` will not work against Windows, you use `ansible.windows.win_copy`.
- **Authentication and PowerShell.** You authenticate as a local Windows account. You'll run into PowerShell's execution policy and into how your credentials are passed; read the Windows setup guide rather than guessing. Keep any password in `ansible-vault`, not in the inventory in the clear.

### Systems-thinking lens

The boundary between WSL and Windows is a trust boundary, exactly the kind you named in Module 0. Until now every boundary you crossed was inside one operating system. This one crosses from Linux to Windows, and the connection plus the authentication *is* the contract between the two sides. When the connection fails, you're not debugging Ansible; you're debugging the contract. That reframing saves you hours: ask "can I even open this connection and authenticate?" (test with `win_ping`) before you ask "why didn't my task run?".

The second lesson is about feedback loops, and it's a lesson by absence. In Module A1 you had Molecule: spin up a throwaway container, converge, assert, destroy, all in seconds. That tight loop is why the Linux module felt safe to experiment in. You do not have that loop here. There is no throwaway Windows container on a Linux Docker host the way there is for Linux, so the cheap, fast, disposable test target is gone. This is worth sitting with: a lot of what made Module A1 pleasant was the feedback loop, not the tool.

When the fast loop isn't available, you don't get to skip testing; you change how you test. You lean harder on check mode (`--check`), you keep the blast radius small (configure one harmless thing first, not your whole machine), and you treat every run against a real target as something to verify by hand afterward. If you later want a real test loop for Windows, it comes from a disposable Windows VM, not a container, which costs more to stand up. That cost difference is a systems-thinking fact about the platform, not a gap in your skills.

### Resources

- Windows guides (setup, connection, FAQ): https://docs.ansible.com/ansible/latest/os_guide/windows.html
- `ansible.windows` collection: https://docs.ansible.com/ansible/latest/collections/ansible/windows/index.html
- `community.windows` collection: https://docs.ansible.com/ansible/latest/collections/community/windows/index.html
- OpenSSH Server on Windows (Microsoft): https://learn.microsoft.com/windows-server/administration/openssh/openssh_install_firstuse
- WSL networking, including mirrored mode: https://learn.microsoft.com/windows/wsl/networking

### Hands-on

Getting the connection working is the gate. Actually configuring your machine is the goal. Do the first three steps to get through the gate, then build a real workstation-setup playbook that leaves your Windows machine genuinely configured.

1. On Windows, enable the OpenSSH Server optional feature and start its service. Confirm you can SSH to the Windows host from inside WSL with a plain `ssh` command *before* you bring Ansible into it. If plain SSH doesn't connect, Ansible won't either, and now you know where the problem is.
2. In `ansible/inventory/`, add the Windows host with the connection variables it needs (the host address you found, `ansible_connection`, `ansible_user`, `ansible_shell_type: powershell`, and so on). Follow the Windows setup guide for the exact variables for your transport.
3. Run `win_ping` against the host. Getting `pong` back means the connection and authentication both work. Do not move on until this passes. This is the milestone.

Now the real work. Write a playbook under `ansible/playbooks/` that configures your actual Windows desktop, the way you'd want a fresh machine set up. This should do real things to your machine, not a token file-create. Aim for a provisioning playbook with several of these, grouped into a role once it grows:

4. **Install the apps you actually use.** Use `win_package`, or a package manager module (`winget`, or `chocolatey.chocolatey.win_chocolatey` if you install Chocolatey). Pick a real list: a browser, a terminal, an editor, whatever you reinstall every time you get a new machine. This is the task that makes the playbook worth keeping; it's your machine, set up the way you want it.
5. **Set system and user preferences you care about.** Registry-backed settings via `win_regedit` (show file extensions in Explorer, dark mode, power plan), environment variables via `win_environment`, a Windows optional feature or capability via `win_optional_feature`. Each one is a real change to how your machine behaves.
6. **Manage a service or a scheduled task.** Disable a service you don't want running, or create a scheduled task with `community.windows.win_scheduled_task` (a nightly cleanup, a backup). These are the "set it the way I like and keep it that way" changes.
7. **Lay down config files.** Push a dotfile or an app config to your Windows user profile with `win_copy` or `win_template` (a Windows Terminal `settings.json`, a `.gitconfig`, an editor config).

Run the whole thing with `--check --diff` first and read what it intends to change before you let it touch your machine. Then run it for real. Then run it a second time and confirm every task reports no change; the idempotency rule from Module A1 holds on Windows too, and this is where you prove it against a machine you care about. Build the playbook up a few tasks at a time rather than all at once, so when something misbehaves you know which task did it. Keep each change reversible by hand, and keep any password in `ansible-vault`.

The target: a playbook you'd genuinely run on your next Windows reinstall. That's the difference between a toy and a tool.

### Deliverable

- Under `ansible/`: the inventory entry and the Windows provisioning playbook (promoted to a role if it grew one), committed. It should do real configuration: apps installed, preferences set, at least one service or scheduled task, at least one config file. Keep any secret in `ansible-vault`, and do not commit plaintext credentials.
- `journal/ansible-windows.md`: how WSL reaches your Windows host (the address and how you found it), which transport and auth you chose and why, the `win_ping` success, a list of what the playbook actually changes on your machine, and the twice-run output showing the second run made no changes. Write down what broke on the way to `pong`; for most people that's the real content of this module.

---

## What done looks like

You're done with this track when:

- You can explain, without looking it up, why the Ansible control node is Linux and your Windows box is a managed node.
- You have one Linux role that passes `molecule test`, including the idempotence check, and you can point to the idempotency bug you fixed to make it pass.
- You can state the difference between Ansible's push-once model and the pull-based reconciliation of Kubernetes and Flux, and say when you'd want each.
- `win_ping` returns `pong` from WSL to your Windows host, and you have a playbook that actually provisions your Windows desktop (apps, preferences, a service or scheduled task, a config file) and does so idempotently.
- Both journal entries are pushed, and someone could reproduce your setup from them.

If `win_ping` is still failing, that's where to spend your time; it's the whole boundary in one command. Bring it to your mentor at the next 1:1 with the exact error, not a paraphrase.
