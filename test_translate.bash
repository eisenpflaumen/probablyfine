#!/bin/bash

##key is stored OUTSIDE the project directory. Never add the key to the project itself.
KEY=$(cat ../probably_fine_translation_api_key.txt) 

text="My hovercraft is full of eels."

payload="{'q': '$text', 'target': 'fr' }"

echo "testing translation of text: $text"
echo "json payload: $payload"
curl -X POST \
  "https://translation.googleapis.com/language/translate/v2?key=$KEY" \
  -H "Content-Type: application/json" -d "$payload"


echo "  ...for now assume that bad rendering of fancy foreign characters is a bash issue."
