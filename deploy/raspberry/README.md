# Despliegue en Raspberry Pi (systemd)

Usuario `hueterman`, proyecto en `/home/hueterman/Data_Basket` (ajusta las rutas
de los `.service` si usas otros).

```bash
cd ~/Data_Basket
sudo cp deploy/raspberry/databasket-*.service deploy/raspberry/databasket-ingest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now databasket-api databasket-ui databasket-ingest.timer
```

- API (puerto 5051) y interfaz (5050): arrancan solas con la Pi y se relanzan si caen.
- `databasket-ingest.timer`: ingesta semanal (lunes 04:00). Ver cuando toca:
  `systemctl list-timers databasket-ingest.timer`
- Lanzarla ya, a mano: `sudo systemctl start databasket-ingest.service`
- Ver su log en vivo: `tail -f ~/Data_Basket/logs/ingest_current_seasons/*.log`
- Logs de los servicios: `journalctl -u databasket-api -f` (o `-ui`, `-ingest`)

Importante: la ingesta escribe en `data/processed/basketball.db` DE LA PI. Si la
sigues ejecutando tambien en el PC (Windows Task Scheduler), las dos bases de datos
divergen: elige un unico sitio donde se ingiere.

## Copias de seguridad de la base de datos

`scripts/backup_db.py` hace una copia **consistente** de `data/processed/basketball.db`
(API de backup de SQLite, segura con la API/ingesta en marcha), la verifica con
`PRAGMA integrity_check`, la comprime (`.db.gz`) y poda las antiguas
(ultimos 7 dias, 4 semanas y 3 meses). Destino: `~/backups/data_basket`
(o la variable `BACKUP_DIR`).

Instalacion (una vez):

```bash
cd ~/Data_Basket
sudo cp deploy/raspberry/databasket-backup.service deploy/raspberry/databasket-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now databasket-backup.timer
sudo systemctl start databasket-backup.service        # primera copia ya
journalctl -u databasket-backup.service -n 20 --no-pager
systemctl list-timers databasket-backup.timer
```

Comprobar:

```bash
.venv/bin/python scripts/backup_db.py --list      # copias existentes
.venv/bin/python scripts/backup_db.py --verify    # verifica la mas reciente
```

Restaurar (para los servicios, sustituye la BD, arranca):

```bash
sudo systemctl stop databasket-api databasket-ui
cd ~/Data_Basket/data/processed
mv basketball.db basketball.db.antes_de_restaurar
rm -f basketball.db-wal basketball.db-shm
gunzip -c ~/backups/data_basket/basketball_AAAA-MM-DD_HHMM.db.gz > basketball.db
sudo systemctl start databasket-api databasket-ui
```

Notas importantes:
- Si las copias estan en el **mismo SSD** protegen de borrados, errores y corrupcion,
  pero **no de que el disco falle**. Baja de vez en cuando una al PC (PowerShell):
  `scp hueterman@192.168.1.59:backups/data_basket/basketball_AAAA-MM-DD_HHMM.db.gz D:\Backups\`
  (o apunta `BACKUP_DIR` a un USB).
- Los modelos `models/*.joblib` no estan en git ni en esta copia: guardalos aparte.
