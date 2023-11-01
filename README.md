# Git_with_me
This repo is for practicing Git

// Authentication for all repos 
git config --global user.name "<github_name>" 
git config --global user.password "<github_name>"

// Authentication for single repos 
git config --local user.name "<github_name>" 
git config --local user.password "<github_name>"

cd 
git init 
git add . 
git status -s 
git commit -m "initial revision" 
git status -s 
git remote add origin <repo_url> 
git remote -v

git push -u origin master
