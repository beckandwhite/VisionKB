# Use case 1:
I have a folder with 20 pictures...

How to start?
I'd like to have a single entrypoint - a command with a single parameter - which is a folder absolute path

Expectations: Output is a single webapplication usable 5 minutes after the quickstart, fully populated in the first hour.

The UI should focus on the timeline filter, make the annotation readable and searchable as Vision tech demo!

No ENV concept is needed for this, or it can be in an ADHOC folder! Or it can be the .workspace itself!


# Usecase 2
Same as UC1 but with 100 pictures

Expectations: Output is a single webapplication usable 5 minutes after the quickstart, fully populated a first hour later.

No ENV concept is needed for this, or it can be in an ADHOC folder! Or it can be the .workspace itself!


The UI should focus on the timeline filter, make the annotation readable and searchable, but not just as Vision techdemo, but also extracting Names, Locations etc - more agentic demo!

The backlog visualisation becomes important here as the enduser will have to wait for the processing!

# Usecase 3:
I'm a power user with 1000 pictures - some of them are photos, not screenshots

Expectations: Output is a webapplication usable 5 minutes after the quickstart, fully populated in a few days
(Nightly cron job(s), throttling, power mode respect etc.
+The datalake itself - accessible for further analyis

ENV concept can be useful - but needs rethinking - maybe the per worker ability to reset is more useful here!


The backlog visualisation and nightly scheduleing gets important here as the enduser will have to wait for the processing and most probably would like to understand what is the bottleneck. (for each worker), ETA etc.


# Usecase 4:
You are a poweruser and would like to test various (still local, OLLAMA hosted) vision models

Simple config edit? External Telemetry?







Data engineering workflow so far:

1. Tracker - populates the tracker from the folder parameter
2. worker1 Whats on this picture - generic annotation.json
3. NER - extract names, locations, organisations, projects, products from the annotations.
4. Use those as Tags
5. correlation check, hierarchy between the tags?
6. Present the tags on the WebUI as filters? (worker4 - Thumbnails)

7. Worker2 - OCR longer analysis - will be slow!
8. Present it on the UI as full-text-searchable - contains/begin UI needs to be good!

9. worker5 - hash value for each files (will be more important for Photos) and other tricks for deduplication - compare text from Annotation1.json? Compare how close they are to each other in terms of capture time (file create time?) 

10. any sort of classification - worker3 - can be bypassed for short term!



