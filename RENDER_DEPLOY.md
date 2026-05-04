# Deploying to Render (Free)

## Step 1: Push Code to GitHub

Since you have already created the repository `https://github.com/maksymusmax/nocorny-voice.git`, follow these steps in your terminal:

1.  **Configure Git** (if you haven't already):
    ```bash
    git config --global user.email "your_email@example.com"
    git config --global user.name "Your Name"
    ```

2.  **Initialize and Push**:
    ```bash
    git init
    git add .
    git commit -m "Initial commit"
    git branch -M main
    git remote add origin https://github.com/maksymusmax/nocorny-voice.git
    git push -u origin main
    ```

## Step 2: Create Web Service on Render

1.  **Sign up/Login**: Go to [render.com](https://render.com).
2.  **New Web Service**: Click "New +" -> "Web Service".
3.  **Connect Repo**: Select `nocorny-voice`.
4.  **Settings**:
    - **Name**: `nocorny-voice`
    - **Runtime**: Python 3
    - **Build Command**: `apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt`
    - **Start Command**: `python bot.py`
    - **Plan**: Free

    > **Why ffmpeg?** The bot splits long audio (>4 min by default) into chunks
    > before sending to Gemini, which prevents the model from looping/hallucinating
    > on multi-minute files. Without `ffmpeg`, long files still work but go through
    > the single-shot path and may fail with `transcribe_degraded`.
5.  **Environment Variables**:
    - `GEMINI_API_KEY`: (Copy from your .env file)
    - `TELEGRAM_BOT_TOKEN`: (Copy from your .env file)
    - `WEBHOOK_URL`: **Leave this blank for now** (You will get this URL *after* you create the service).

## Step 3: Finalize Webhook

1.  **Wait for Deploy**: Render will start deploying. It might fail or just sit there because the Webhook URL is missing. That's okay!
2.  **Get the URL**: Look at the top-left of the Render dashboard (under the service name). You will see a URL like `https://nocorny-voice.onrender.com`. **Copy it.**
3.  **Update Variable**:
    - Go to the **Environment** tab.
    - Add/Update `WEBHOOK_URL` with the URL you just copied.
    - Click **Save Changes**.
4.  **Redeploy**: Render will restart the bot automatically. Now it will work!
