#!/bin/sh
sed "s/ \+//g" "$1" | sed 's/"pom-location-uri":"[^"]\+",//g' | sed 's/"source-uris":\[[^]]\+\],//g' | tr -d '\n' | sed 's/,"idx":[0-9]\+//g'
