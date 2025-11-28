## 📂 Struttura del Progetto

* `invoice_Agent.py`: Il codice sorgente principale dell'applicazione.
* `LanciaFatture.bat`: Script per l'avvio rapido su Windows.
* `.venv/`: Cartella dell'ambiente virtuale Python (non modificare).
* `.gitignore`: File di configurazione per escludere file temporanei e di build dal repository Git.

## 📦 Creare un Eseguibile (.exe)

Se vuoi distribuire l'applicazione a chi non ha Python, puoi creare un file `.exe` standalone.

1.  Assicurati di avere il file `launcher.py` nella cartella.
2.  Lancia il seguente comando nel terminale (con ambiente virtuale attivo):

pyinstaller --noconfirm --onedir --windowed --name "EstrattoreFatture" --clean --collect-all streamlit --collect-all pdfplumber --collect-all pandas --add-data "invoice_Agent.py;." launcher.py


L'eseguibile si troverà nella cartella `dist/EstrattoreFatture/`.

## 📝 Logica di Estrazione (Dettagli)

Il sistema utilizza **Regex** (Espressioni Regolari) e strategie posizionali:

* **Numero:** Filtra date (gg/mm/aaaa), numeri civici (se preceduti da "Via") e parole come "Pagina". Preferisce codici alfanumerici (es. "64/E").
* **Importo (Compensi):** Cerca nella riga "Compensi/Onorari" e seleziona il valore numerico più alto (max) per evitare di catturare la quantità "1,00".
* **Spese Generali:** Cerca "15%" o "Spese generali" e prende l'ultimo valore a destra della riga.
* **Totale:** Cerca specificamente la dicitura "Totale Onorari" e prende il primo valore valido trovato.

---
*Sviluppato per uso interno - 2025*