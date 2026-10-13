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
