
This is our bot, a [will](https://github.com/greenkahuna/will) bot.

This one is used at edX, we call him Alton.

## Development

For doing local Alton development, follow these steps:

1. Install Docker.
  * On Linux, you'll need to install Docker Compose separately. See the instructions [here](https://docs.docker.com/compose/install/).
  * On Windows/Mac, use [Docker Toolbox](https://www.docker.com/products/docker-toolbox) to install.
2. Clone the Alton repo:
  * `git clone https://github.com/edx/alton.git`
3. Change into the docker directory:
  * `cd alton/docker`
4. Adjust the environment variables included in `docker-compose.yml`.
  * You'll need HipChat authentication tokens and in which rooms you'd like your development Alton to answer commands.
  * Also, perhaps AWS credentials - depending on what you're developing.
5. Start the project containers:
  * `docker-compose up`
