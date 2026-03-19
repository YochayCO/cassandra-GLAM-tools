module.exports = {
    apps: [
      {
        name: "new-glam-listener",
        script: "new_glam_listener.py",
        interpreter: "venv/bin/python",
        args: "--start_date 2025-03-06",
        env: {
          ENV: "production"
        }
      }
      // ,
      // {
      //   name: "daily",
      //   script: "daily.py",
      //   interpreter: "venv/bin/python",
      //   cron: '0 4 * * *',
      //   'no-autorestart': true,
      //   args: "--start_date 2025-03-06",
      //   env: {
      //     ENV: "production"
      //   }
      // }
    ]
  };
  