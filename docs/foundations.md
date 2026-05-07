# Foundations: Three weeks before Module 0

The main curriculum starts at "what is a platform." That assumes you're already comfortable on the command line, with Git, and with the basic vocabulary of systems. These three weeks build that base. Don't skip them, even if parts feel familiar. The point is to find the gaps before they bite you in week 6.

Each week ends with a `journal/` entry pushed to your fork of the curriculum repo. The journal is the deliverable. Write what you set up, what broke, how you fixed it, and what you didn't understand. Future-you will thank present-you.

---

## Week 0a: Environment, tools, and the developer loop

Goal: a working WSL Ubuntu environment, Git fluency, and the muscle memory to clone, branch, commit, and PR without thinking about it.

### Setup checklist

- WSL2 with Ubuntu 22.04 or later. Run `lsb_release -a` to confirm
- Windows Terminal as your terminal app. Pin it
- VSCode with the WSL extension. Open VSCode from inside WSL by typing `code .` in your project directory
- Git installed (`sudo apt install git`)
- Configure `.gitconfig` with your name and email (`git config --global user.name "Your Name"`, `git config --global user.email "you@example.com"`). If you skip this, your commits will show up as "unknown" and you'll have to rewrite history later
- GitHub account, SSH key generated and uploaded (`ssh-keygen -t ed25519`, then `cat ~/.ssh/id_ed25519.pub` and paste into GitHub settings)
- Create a `.gitignore` in your home directory or your project with at least: `.env`, `node_modules/`, `*.log`. Learn what this file does now so you don't accidentally commit secrets in week 3
- Docker Desktop with the WSL2 backend. Don't worry about rootless Docker yet; that's a Module 1 topic
- Minikube installed and starts cleanly (`minikube start`, then `kubectl get nodes` returns a Ready node)
- A text editor you can survive in. Pick vim or nano. Don't use both. You'll still use VSCode for multi-file work. The terminal editor is for quick single-file edits without leaving the shell. If you pick vim, learn how to quit it before anything else (`:q!`)
- Customize your shell prompt to show the current git branch. Add this to your `~/.bashrc` or `~/.zshrc`: `parse_git_branch() { git branch 2>/dev/null | grep '*' | sed 's/* //'; }` and update your PS1. This prevents wrong-branch mistakes and forces you to understand what a shell config file does

### Skills to build

- Navigate the filesystem without thinking: `cd`, `ls`, `pwd`, `tree` (install it)
- Read files: `cat`, `less`, `head`, `tail -f`
- Find things: `grep`, `find`, `which`, `whereis`
- Manipulate streams: `|`, `>`, `>>`, `<`
- Edit files in your chosen editor without leaving the terminal
- Git basics: `init`, `clone`, `status`, `add`, `commit`, `push`, `pull`, `log`, `diff`
- Git branching: `branch`, `checkout`, `switch`, `merge`, `rebase` (read about it, don't use it yet)
- The fork-and-PR workflow on GitHub: fork a repo, clone your fork, add the original as `upstream`, branch, push, open a PR

### Practice exercise

Fork the public repo `octocat/Hello-World`. Clone your fork. Create a branch called `add-greeting`. Add a file `greetings/yourname.md` with a paragraph about why you're learning this. Push the branch. Open a PR against your fork (not the original, your own fork). Merge it. Notice what shows up in `git log` after.

Then do the same flow against your own curriculum repo.

### Break things on purpose

You're not done with 0a until something has gone wrong and you've recovered. Do all of these:

- Make a commit on `main`, then try to push to a remote branch that doesn't exist. Read the error
- Create a branch, make a commit, push it, then delete the branch locally with `git branch -d`. Now try `git pull`. Read the error
- Edit a file, don't commit, then run `git checkout -- <file>`. The edit is gone. Understand why
- Run `git log --oneline` and pick a commit hash. Run `git checkout <hash>`. Read the "detached HEAD" message. Get back to your branch
- Stage a file with `git add`, then unstage it with `git restore --staged <file>`. Confirm with `git status`

Git errors are where the learning lives. Every one of these will happen to you accidentally later. Better to see them now when the stakes are zero.

### Resources

Read chapter 2 of Pro Git before attempting the practice exercise. Use chapter 3 as a reference when you hit branching and merge questions. Watch the Missing Semester lectures after you've done the hands-on work. They'll reinforce what you just practiced rather than feeling abstract.

- Pro Git, by Scott Chacon and Ben Straub. Free online at git-scm.com/book. Chapters 1, 2, 3
- The Missing Semester of Your CS Education, MIT, free on YouTube. Lectures 1 (shell), 2 (shell tools), 5 (command-line environment), 6 (Git)
- Microsoft's WSL documentation, particularly the "Best practices" section
- Minikube's quickstart at minikube.sigs.k8s.io

### Deliverable

Push `journal/week-0a.md` to your repo. In it: the commands you ran (not just the names of things you installed), one thing that broke from the "break things" section and how you fixed it, and one shell command you didn't know about a week ago. Write it so that future-you could reproduce your setup from the journal alone.

---

## Week 0b: Thinking in Systems, the book

The Meadows book is the systems-thinking foundation for the rest of the curriculum. Every "systems-thinking lens" callout in the main curriculum assumes you've internalized this vocabulary. Read it actively. Take notes. Argue with it where you disagree.

Pace yourself: roughly two chapters per session, three sessions across the week.

**How to read actively:** After each chapter, close the book and write your prompt response from memory. Don't look back. If you can't recall the core idea well enough to answer the prompt, re-read the section you're stuck on. This is retrieval practice. It's uncomfortable, and that's the point. The discomfort is the learning. Highlighting and re-reading feel productive but don't produce recall. Writing from memory does.

### Chapter-by-chapter prompts

You don't have to write a polished essay for each one. Three to five sentences in your journal is enough. The point is to make the concepts portable into your own examples.

**Chapter 1, The Basics.** Stocks, flows, feedback loops. The bathtub model.
Prompt: identify a stock-and-flow system from your own life. Money in your bank account, water in your fridge, items on your todo list. Name the stock. Name the inflows. Name the outflows. Name one balancing feedback loop and one reinforcing loop, if they exist.

**Chapter 2, A Brief Visit to the Systems Zoo.** Several worked examples of simple structures producing surprising behavior.
Prompt: pick one example. Re-explain it in your own words without looking. The math doesn't matter. The structural logic does.

**Chapter 3, Why Systems Work So Well.** Resilience, self-organization, hierarchy.
Prompt: think of a system you depend on that recovers gracefully when something goes wrong (your phone's network, your body's immune system, the power grid). What property of its design gives it that resilience? Now think of one that's brittle. What's missing?

**Chapter 4, Why Systems Surprise Us.** Bounded rationality, nonlinearities, delays, the difference between events and behavior.
Prompt: write about a time you were surprised by a system's behavior. Career, relationship, software, traffic, anything. Map the surprise to one of Meadows' categories.

**Chapter 5, System Traps and Opportunities.** This is the most operationally useful chapter for platform engineering. The traps:

- Policy resistance (fixes that backfire)
- Tragedy of the commons
- Drift to low performance
- Escalation
- Success to the successful
- Shifting the burden to the intervenor (addiction, dependency)
- Rule beating
- Seeking the wrong goal

Prompt: pick three traps. For each, describe somewhere you've observed it. School, family, work, online communities, video games. Don't strain to find tech examples yet. The pattern recognition matters more than the domain.

**Chapter 6, Leverage Points.** Twelve places to intervene in a system, ordered from least to most powerful. Parameters at the bottom (changing a number), paradigms at the top (changing the worldview the system runs on).
Prompt: pick a system you'd like to change. List which leverage points are available to you and which aren't, and why.

**Chapter 7, Living in a World of Systems.** The synthesis chapter. Less prescriptive, more "here's how this changes your default seeing."
Prompt: which of Meadows' suggestions feels easiest for you to adopt? Which feels hardest? Why?

### Capstone for the week

Write a 500-word essay applying Meadows' vocabulary to one system. Pick one of:

- A food delivery app's economy (riders, restaurants, customers, ratings)
- Your current or most recent workplace
- The economy of a multiplayer game you've played
- The traffic patterns near where you live
- Your own daily routine

Use stocks, flows, feedback loops, and at least one delay and one trap, explicitly named. Push it as `journal/meadows-essay.md`. The point isn't to be right. The point is for the vocabulary to start feeling native.

### Why this matters for the rest of the curriculum

When you hit the reconciliation loop in Kubernetes, you'll recognize it as a balancing feedback loop with a delay. When you build a CI pipeline, you'll be optimizing the delay between cause (commit) and effect (signal). When your team accumulates tech debt, you'll recognize "drift to low performance." When a metric starts getting gamed, you'll recognize "rule beating." The vocabulary is the load-bearing structure.

---

## Week 0c: Linux and networking, the gentle version

Module 1 of the main curriculum drops you into namespaces, cgroups, and `tcpdump`. That's later. This week is the everyday Linux you need to operate without flinching.

### Skills to build

- The filesystem hierarchy: what lives in `/etc`, `/var`, `/home`, `/tmp`, `/usr`. You don't have to memorize it, just stop being surprised by it
- Permissions and ownership: read/write/execute, user/group/other, what `chmod 755` means, when you actually need `sudo`
- Processes: `ps aux`, `top` or `htop`, `kill`, the difference between SIGTERM (polite) and SIGKILL (rude)
- Package management: `apt update`, `apt install`, `apt list --installed`. Why you should care what you install
- Networking concepts at the user level: an IP address is a phone number, a port is an extension, DNS is the phonebook. `ping`, `curl`, `dig`
- systemd at the user level: `systemctl status`, `systemctl restart`, `journalctl -u <service>`
- The shell environment: `$PATH`, `$HOME`, `~/.bashrc`, why your changes "don't work" until you `source` it or open a new shell

### Practice, part 1: Linux

Do these before touching minikube. They exercise the skills listed above on your WSL system directly.

- Run `ps aux | grep docker`. Identify the Docker daemon process. What user owns it?
- Start a long-running process in the background (`sleep 300 &`). Find it with `ps`. Kill it with `kill`. Now start another and kill it with `kill -9`. Understand the difference (SIGTERM vs SIGKILL)
- Run `systemctl status docker`. Read the output. Now `journalctl -u docker --since "10 minutes ago"`. What's in there?
- Run `ping -c 3 google.com`. Now `dig google.com`. Now `curl -I https://google.com`. Each tool shows you a different layer of the same request
- Run `ss -tlnp` to see what's listening on your system. Pick a port and `curl localhost:<port>`. Understand what responded
- Create a file owned by root (`sudo touch /tmp/rootfile`). Try to write to it without sudo. Change its permissions so you can. Then change them back

### Practice, part 2: The same things, inside minikube

The point of this section is to see that a Kubernetes cluster is just Linux processes. Everything you did above works inside the cluster too.

- Start minikube. Run `kubectl get pods -A`. Read every line. What's running and why?
- Deploy a hello-world web server as a Deployment. You don't need to understand the YAML deeply yet. Use `kubectl create deployment hello --image=nginx` and `kubectl expose deployment hello --port=80`. That's enough for now
- Curl it from inside the cluster: `kubectl run -it --rm curl --image=curlimages/curl -- sh`, then `curl hello.default.svc.cluster.local`
- Now run `dig hello.default.svc.cluster.local` from inside that same curl pod. Notice who answers (CoreDNS). Compare that to the `dig` you ran on your host earlier
- Read the logs of a running pod with `kubectl logs`
- Exec into a running pod with `kubectl exec -it <pod> -- sh`. Run `ps aux` inside. It's just a Linux process tree
- Break minikube on purpose. Delete a system pod (`kubectl delete pod -n kube-system <pick-one>`) and watch it come back. Stop the cluster, start it again, watch what survives. The point is to lose the fear of breaking things

### Resources

Do the Linux practice exercises first, then use these resources to fill gaps. Linux Journey is best read alongside the exercises, not before them. Julia Evans' DNS zine is worth reading after you've run `dig` yourself and have questions about what you saw.

- Linux Journey at linuxjourney.com. Free, well-paced, hands-on
- Julia Evans' zines, particularly "How DNS Works" and "Bite-Size Linux." Buy them, they're worth it
- The minikube handbook section on common commands

### Deliverable

Push `journal/week-0c.md` with: the output of `kubectl get pods -A` after you deployed your hello-world, the output of `dig hello.default.svc.cluster.local` from inside the cluster, plus one paragraph explaining what each system pod is roughly responsible for. Also `kubectl describe` one pod you don't understand and write what you learned from the output. You'll be wrong about some of it. Ask your mentor at the next 1:1.

---

## What "ready for Module 0" looks like

You're done with foundations when:

- Opening a terminal, navigating to your repo, branching, editing a file, committing, and pushing takes you under a minute and feels boring
- You can describe a stock, a flow, a balancing loop, and a reinforcing loop without looking them up
- You can deploy something to minikube, see it running, and know how to start debugging when it's not
- You can name three Meadows traps and one example of each from your own life

If any of those feels shaky, spend another week before starting Module 0. The compression of the main curriculum is fake urgency. The foundation is what makes the rest of the curriculum stick.
