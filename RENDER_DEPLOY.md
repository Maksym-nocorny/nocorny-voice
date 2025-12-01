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
    - **Build Command**: `pip install -r requirements.txt`
    - **Start Command**: `python bot.py`
    - **Plan**: Free
5.  **Environment Variables**:
    - `GEMINI_API_KEY`: (Copy from your .env file)
    - `TELEGRAM_BOT_TOKEN`: (Copy from your .env file)
    - `WEBHOOK_URL`: `https://nocorny-voice.onrender.com` (or whatever URL Render assigns you).

## Step 3: Finalize Webhook

Once deployed, Render will give you a URL (e.g., `https://nocorny-voice.onrender.com`).
1.  Go to the **Environment** tab in Render.
2.  Add/Update the `WEBHOOK_URL` variable with this value.
3.  Render will restart the bot, and it will start listening for webhooks!
