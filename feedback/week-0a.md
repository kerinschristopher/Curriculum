Good start. You showed up, made a choice on vim, and pushed the file. Three notes for next time:

## Break something

The deliverable asks for "one thing that broke and how you fixed it" and you said nothing's broken yet. That means you're being too careful. Before you move to 0b: run `minikube delete`, then `minikube start` again. Or `docker rm -f` a running container. Or `git checkout` a file you just edited. The goal is to learn that breaking things is cheap and recoverable. If nothing breaks on its own, break it on purpose.

## Drop GitHub Desktop

The curriculum is CLI-only for a reason. The muscle memory of `git add`, `git commit`, `git push` is what makes branching and PRs feel boring by week 3. A GUI skips the part where you internalize the model. Uninstall it or at least stop opening it. If you get stuck, `git status` will always tell you what to do next.

## Write commands, not summaries

This journal is for future-you. "I installed Docker" won't help you six weeks from now when something breaks. "I ran `sudo apt install docker.io`, got a permissions error, fixed it with `sudo usermod -aG docker $USER`, then had to log out and back in." That's a journal entry you'll actually reference. Next time, paste the commands you ran and what happened.
