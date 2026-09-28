FROM python:3.11-slim

COPY app/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt --extra-index-url https://www.piwheels.org/simple

COPY app /app
WORKDIR /app

ENV UCP_DATA_DIR=/app/data
EXPOSE 8000/tcp

LABEL version="0.1.0"

ARG IMAGE_NAME=buckleys-ucp1d

LABEL permissions='\
{\
  "ExposedPorts": {\
    "8000/tcp": {}\
  },\
  "HostConfig": {\
    "Privileged": true,\
    "Binds": [\
      "/usr/blueos/extensions/$IMAGE_NAME:/app/data",\
      "/dev:/dev"\
    ],\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "PortBindings": {\
      "8000/tcp": [\
        {\
          "HostPort": ""\
        }\
      ]\
    }\
  }\
}'

ARG AUTHOR
ARG AUTHOR_EMAIL
LABEL authors='[\
    {\
        "name": "$AUTHOR",\
        "email": "$AUTHOR_EMAIL"\
    }\
]'

ARG MAINTAINER
ARG MAINTAINER_EMAIL
LABEL company='{\
        "about": "Buckleys UCP1D underwater corrosion probe integration",\
        "name": "$MAINTAINER",\
        "email": "$MAINTAINER_EMAIL"\
    }'
LABEL type="device-integration"
LABEL tags='["cathodic-protection", "corrosion", "rs485", "sensor", "data-logging"]'
ARG REPO
ARG OWNER
LABEL readme='https://raw.githubusercontent.com/$OWNER/$REPO/{tag}/README.md'
LABEL links='{\
        "source": "https://github.com/$OWNER/$REPO",\
        "website": "https://www.buckleysinternational.com"\
    }'
LABEL requirements="core >= 1.1"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
