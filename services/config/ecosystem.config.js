module.exports = {
    apps: [
      {
        name: "fill-gaps",
        script: "fill_gaps.py",
        interpreter: "python3",
        args: "--start_date 2025-03-06",
        env: {
          ENV: "production"
        }
      }
    ]
  };
  