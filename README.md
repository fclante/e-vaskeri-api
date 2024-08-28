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

## Configuation 

Set a secret on the repository used for the API. The secret should be named `E_VASKERI_PASSWORD` and contain the password for your user on the E-Vaskeri API.
Set a secret on the repository used for the API. The secret should be named `E_VASKERI_USERNAME` and contain the username for your user on the E-Vaskeri API.