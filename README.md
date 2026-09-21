# ha-schoolhub

SchoolHub is a Python 3-based integration for retrieving school-related
information from **Drei Köche** and **SWOP**. It can be run locally or
integrated into **Home Assistant**.

## Requirements

-   Python 3
-   Valid login credentials for the supported services
-   For Home Assistant integration: access to Python scripts and
    automations

## Configuration

Before running the scripts, configure your login credentials in:

``` text
config.yaml
```

Only the relevant credentials need to be added there.

## Available Fetch Scripts

### Drei Köche

For Drei Köche, only the following script is required:

``` text
fetch_drei_koeche.py
```

### SWOP

For SWOP, the following scripts are sufficient:

``` text
fetch_swop_news.py
fetch_swop_timetable.py
```

The remaining components are assembled and used together by the
respective scripts and configuration.

## Local Usage

When the fetch scripts are executed locally, an `output` directory is
generated automatically.

The directory contains the resulting `.json` files, organized by child
where applicable.

Example structure:

``` text
output/
├── *.json
```

The exact files depend on the selected fetch script and the data
returned by the service.

## Home Assistant Integration

For a Home Assistant installation, place the project files in:

``` text
/homeassistant/scripts/schoolhub
```

The Python scripts can then be executed directly through Home Assistant
automations.

In the Home Assistant setup, the generated `.json` files are stored in:

``` text
/homeassistant/www/schoolhub
```

Files placed in this directory are made available locally by Home
Assistant and can be retrieved directly using HTTP `GET` requests.

This allows other Home Assistant components, dashboards, or integrations
to consume the generated data.

## Typical Workflow

1.  Add the required login credentials to `config.yaml`.
2.  Place the project files in `/homeassistant/scripts/schoolhub`.
3.  Configure Home Assistant automations to run the relevant Python
    fetch scripts.
4.  The scripts retrieve the data and generate `.json` files.
5.  The generated files are saved to `/homeassistant/www/schoolhub`.
6.  The JSON files can be accessed locally through HTTP `GET` requests or can be used direcly inside Home Assistant.

## License

Add the applicable license information here.
