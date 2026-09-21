# InfraCrackNet - Web App

## How to run this on your computer

1. Open a terminal inside the `app` folder.
2. Install the required packages (only needs to be done once):
   ```
   pip install -r ../requirements.txt
   ```
3. Start the website:
   ```
   uvicorn main:app --reload
   ```
4. Open your browser to: http://127.0.0.1:8000

## Demo accounts (already created for testing)

| Name | Role | Employee Code |
|---|---|---|
| Ali Raza | Inspector | INS-0156 |
| Fatima Noor | Engineer | ENG-0089 |
| Bilal Ahmed | Inspector | INS-0161 |
| Sana Malik | Engineer | ENG-0093 |
| Usman Tariq | Admin | ADM-0004 |

The first time you use any of these, go to the Sign Up page, enter the Employee Code, and set
your own password. After that, log in normally with the email and that password.
