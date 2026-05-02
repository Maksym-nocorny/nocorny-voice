# How to Create a Telegram Bot and Get Your Token

Follow these step-by-step instructions to get your **Telegram Bot Token**.

## Step 1: Find BotFather

1.  Open the **Telegram** app (Mobile or Desktop).
2.  Tap the **Search** icon (magnifying glass).
3.  Type `@BotFather`.
4.  Select the verified account (it has a blue checkmark ☑️).

## Step 2: Create a New Bot

1.  Tap the **Start** button at the bottom of the chat (or type `/start`).
2.  Send the command:
    ```text
    /newbot
    ```
3.  **BotFather** will ask: *"Alright, a new bot. How are we going to call it? Please choose a name for your bot."*
    - **Action**: Type a display name (e.g., `My AI Transcriber`). This is what users will see in their chat list.

4.  **BotFather** will ask: *"Good. Now let's choose a username for your bot. It must end in `bot`. Like this, for example: TetrisBot or tetris_bot."*
    - **Action**: Type a unique username (e.g., `MaksymTranscribeBot`).
    - *Note*: If the name is taken, BotFather will tell you. Just try a different one (e.g., add numbers like `MaksymTranscribe2024Bot`).

## Step 3: Copy Your Token

1.  Once you choose a valid username, **BotFather** will reply with a message starting with: *"Done! Congratulations on your new bot..."*
2.  In that message, find the section that says:
    > Use this token to access the HTTP API:
3.  You will see a long string of characters that looks like this:
    `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`
4.  **Copy this entire string**. This is your **API Token**.

## Step 4: Add Token to Your Project

1.  Go back to your code editor.
2.  Open the file named `.env`.
3.  Find the line `TELEGRAM_BOT_TOKEN=`.
4.  Paste your token there. It should look like this:
    ```env
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
    ```
5.  **Save** the file.

## Step 5: Start Your Bot

1.  Open your terminal.
2.  Run the bot:
    ```bash
    python bot.py
    ```
3.  Go back to Telegram and search for your bot's username (the one you created in Step 2).
4.  Click **Start** and send a voice message!
