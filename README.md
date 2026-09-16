# Pokémon GO Map

Desktop client for the public Campfire GraphQL used by [pokemongo.com/map](https://pokemongo.com/en/map). No Pokémon GO / Wayfarer login required.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pogo_official
```

Enter coordinates, pick a radius, and search. The default region is a circle.
