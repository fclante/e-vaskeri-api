# E-Vaskeri API

This is a wrapper API for the E-Vaskeri API. The E-Vaskeri API is a service that provides information about the availability of washing machines and dryers in the laundry rooms of my apartment complex.

## Endpoints

### GET /login

This endpoint is used to log in to the E-Vaskeri API. It requires a username and password as query parameters.

### GET /running-machines

This endpoint returns a list of all running machines in the apartment complex.

### GET /status

This endpoint returns the status of the E-Vaskeri API.


### GET /balance

This endpoint returns the balance of the user.

---

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                   # create .venv and install locked dependencies
uv run gunicorn --chdir src main:app -c gunicorn.conf.py   # run the API
uv add <package>                          # add a dependency (updates pyproject.toml and uv.lock)
uv lock --upgrade                         # upgrade locked versions
```

## Configuation 

pass 2 environment variables: `E_VASKERI_USERNAME` and `E_VASKERI_PASSWORD` when starting the container or running the API.
`E_VASKERI_USERNAME` should contain the username for your user on the E-Vaskeri API.
`E_VASKERI_PASSWORD` should contain the password for your user on the E-Vaskeri API.

