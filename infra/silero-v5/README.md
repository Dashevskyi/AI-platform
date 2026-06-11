# silero-v5 TTS service (.9:8006)

Silero v5_cis_base (MIT — commercial OK), 60 speakers (ru_saida / ukr_roman defaults), 48kHz.
With stress accentuators: ruaccent (ru, homograph-aware) + ukrainian-word-stress (uk).

Deploy on 172.10.100.9:
  model: /srv/silero-v5/v5_cis_base.pt  (https://models.silero.ai/models/tts/ru/v5_cis_base.pt)
  docker build -t silero-v5:mit .
  docker run -d --name silero-v5 --gpus '"device=1"' --restart unless-stopped \
    -p 172.10.100.9:8006:8080 -v /srv/silero-v5:/model:ro -v /srv/silero-v5-hf:/root/.cache silero-v5:mit

API: POST /tts {text, lang: ru|ua, speaker?, sample_rate?, format: wav|asterisk, accent: true}
     GET /speakers, GET /health
