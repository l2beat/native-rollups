# The daisugi genesis generator writes the Daisugi fork into the execution
# genesis (eip8288PrototypeTime), but its consensus config template has no
# DAISUGI_FORK_VERSION or DAISUGI_FORK_EPOCH, so Lighthouse never forks. This
# image appends both; the generator fills them from its environment.
FROM ethpandaops/ethereum-genesis-generator:daisugi-devnets@sha256:b32f0b7d9e5d96d9b5dad2c416c010f78764c6c88854e6b12520c487b2a0211d
RUN printf '\nDAISUGI_FORK_VERSION: $DAISUGI_FORK_VERSION\nDAISUGI_FORK_EPOCH: $DAISUGI_FORK_EPOCH\n' >> /config/cl/config.yaml
