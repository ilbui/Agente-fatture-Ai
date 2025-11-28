@echo off
:: Spostati nella cartella dove si trova questo file .bat
cd /d "%~dp0"

:: Attiva l'ambiente virtuale
call .venv\Scripts\activate.bat

:: Lancia l'applicazione Streamlit
streamlit run invoice_Agent.py

:: Se c'è un errore, lascia la finestra aperta per leggerlo
if %errorlevel% neq 0 pause