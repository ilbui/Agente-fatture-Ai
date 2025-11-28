import sys
import os
from streamlit.web import cli as stcli

def resolve_path(path):
    """Trova il percorso del file anche quando è compilato in .exe"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.path.abspath("."), path)

if __name__ == '__main__':
    # Dice a Streamlit di eseguire il tuo script principale
    sys.argv = [
        "streamlit",
        "run",
        resolve_path("invoice_Agent.py"), # Il tuo file originale
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())