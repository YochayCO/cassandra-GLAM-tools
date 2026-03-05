# GLAM Wiki Dashboard

The purpose of this project is to Support GLAMs* in monitoring and evaluating
their cooperation with Wikimedia projects. Starting from a Wikimedia Commons
category this tool collects data about usage, views, contributors and topology
of the files inside.

** GLAM - galleries, libraries, archives and museums

## Structure

The project is split into two packages - app & services.

* app - includes an express.js server that functions both as an API and as a front-end server (using mostly handlebars as render engine)
* services - includes python & node.js scripts for ETL (extract, transform & load) and recommendations (not currently active). The ETL includes a new GLAM loader (for when adding a new GLAM to the system), and a daily cron job to updated the analytical data of yesterday for all of the GLAMs in the system.

## Installation - app

Enter the /app folder

```bash
cd app
```

Install Node.js project dependencies:

```bash
npm install
```

**Add a production config file inside the config folder: `./config/config.production.json` With the same structure as in `./config/config.sample.json`**

export ENV:

```bash
export ENV="production"
```

Run the local server:

```bash
pm2 start server.js
```

You can see the logs by running th following command:

```bash
pm2 logs
```

## Installation - services

Enter services folder

```bash
cd services
```

Install Python dependencies:

```bash
pip3 install -r requirements.txt
npm install
```

export ENV:

```bash
export ENV="production"
```

**Add a production config file inside the config folder: `./config/config.production.json` With the same structure as in `./config/config.sample.json`**


### Run initdaily.sh - script that run dail.py. <br /> Run it with the following commands to do it with a daily cron job
The daily script runs every day at 4:00 AM

### Run daily cronjob

```bash
export ENV=production
pm2 start daily.py --cron '0 4 * * *' --interpreter python3 --no-autorestart -- -e production
```

### Save the process

```bash
pm2 save
```

A Logs folder will be created, every day  at 4:00 AM a new file will be added with the date of the day, where you can always see the logs of the daily.  <br >

### Run new glam listener

```bash
export ENV=production
pm2 start new_glam_listener.py --interpreter python3 -- -e production
```

You can see the logs by running th following command:

```bash
pm2 logs
```

## Starting local postgres instance using docker compose

```bash
docker-compose up -d
```

# FLOW

## Adding a new organization

1. User submits a response in the google form
2. Supporter (that's you) gets an email notification (set it up in advance...)
3. Verify the user's response validity
4. Log in to the WikiGlams control panel. Use the details from the production config file.
5. **Make sure that the `new_glam_listener` is running**
6. **Create a new GLAM** with the relevant details of the organization
7. Make sure that the `new_glam_listener` finishes the job

## Filling gaps
If the daily service was down for a few days we need to fill the gaps from the last date that was scanned.

```bash
export ENV=production
pm2 start fill_gaps.py --interpreter python3 -- --start-date %Y-%m-%d -e production
```

Date format is 2025-04-20. Start-date is mandatory.
Important: Script loads for a minute or two and only then starts logging.


About the `config.[env].json` file format:
* `postgres`: Read from postgres
* `postgresWrite`: Write to postgres
* `wmflabs`: Connect to mariadb - to be able to fetch wikicommons analytics (of categories and all the important data). Accessible only from wiki cloud 
* `admin`: Admin credentials (for the Admin Panel)
* `aws`
* `glamUser`
* `mediacountStartDate`
* `limits`

app config also has:
* `newGlamServiceEndpoint`: The endpoint to which the app sends a post request, when adding a new GLAM

About `new_glam_listener.py`

* Needs access to postgres, mariadb
* mariadb not accessible from localhost - it is on wiki cloud

## For future caution

* Note issue https://phabricator.wikimedia.org/T396724 . Particularly, if posgresql is ever updated from 12.7, it might reset its settings and archive might blow up again.

## Useful sources for support
* [Subscribe to the announcements from wikimedia support for important server notifications](https://lists.wikimedia.org/postorius/lists/cloud-announce.lists.wikimedia.org/)
* They also appear in the [News section in WikiTech](https://wikitech.wikimedia.org/wiki/News)
* If you ever need to add a new server / more resources - this is [the location to ask for additional quota](https://phabricator.wikimedia.org/project/view/2880/). This is rare and should not be used for most issues even if they involve low resources.
* Telegram channel - Very helpful direct line of human support. Used it whenever I couldn't solve something on my own. They have permissions that we don't.
* More formally, they work with [Phabricator](https://phabricator.wikimedia.org/) - but some issues don't even deserve to be opened there.
* Some helpful documentation is in [Wikitech](https://wikitech.wikimedia.org/)
* Our servers are managed in [Horizon Wikimedia cloud services](https://horizon.wikimedia.org/). Default project on login is alphabetically ordered, so make sure that you are on the right project and not on bastion - the mediating/center server (for ssh and more)
* Toolforge - it exists but I'm not sure what we need it for if at all.


## Notes to self
Making changes is currently hard.
I don't have time to set up a local environment - it's not easy enough. 
So I'm Cowboy-programming. 
And another issue: My regular yochayco linux user has no permissions to edit files.
So I'm either using vim, or doing 'save as' in `/tmp` folder and then copying from `mn__` user.
And the worst part: This `mn__` user can't push to remote git.
I've cloned the project and I can update it with `yochayco` but the local repo is out of sync since `yochayco` won't write to `.git` folder.
From the cloned project I'm making PRs to yonatanmil but there is no real reason to do that I guess.
