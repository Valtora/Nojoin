<!-- markdownlint-disable -->

# Beam

## Docs

- [Building AI Agents](https://docs.beam.cloud/v2/agents/introduction.md): Beam is launching a new type of agent framework that is stateful and has concurrency built-in.
- [Example: Research Assistant](https://docs.beam.cloud/v2/agents/synchronization.md)
- [Mounting S3 Buckets](https://docs.beam.cloud/v2/data/external-storage.md): Attach S3 buckets to your apps
- [Ephemeral Files and Images](https://docs.beam.cloud/v2/data/output.md): Storing ephemeral files for images, audio files, and more.
- [Distributed Storage Volumes](https://docs.beam.cloud/v2/data/volume.md): Attach distributed storage volumes to your apps
- [Keeping Containers Warm](https://docs.beam.cloud/v2/endpoint/keep-warm.md): Control how long your apps stay running before shutting down.
- [Pre-Loading Models](https://docs.beam.cloud/v2/endpoint/loaders.md): This guide shows how you can optimize performance by pre-loading models when your container first starts.
- [Creating a Web Endpoint](https://docs.beam.cloud/v2/endpoint/overview.md): Deploying and invoking web endpoints on Beam
- [Realtime and Streaming](https://docs.beam.cloud/v2/endpoint/realtime.md)
- [Sending File Payloads](https://docs.beam.cloud/v2/endpoint/sending-file-payloads.md): Sending file payloads to Endpoints and Web Servers
- [Versioning](https://docs.beam.cloud/v2/endpoint/versioning.md)
- [Hosting a Web Server](https://docs.beam.cloud/v2/endpoint/web-server.md): Deploying web servers on Beam
- [Container Images](https://docs.beam.cloud/v2/environment/custom-images.md)
- [Custom Registries](https://docs.beam.cloud/v2/environment/custom-registries.md)
- [GPU Acceleration](https://docs.beam.cloud/v2/environment/gpu.md)
- [Working in Jupyter Notebooks](https://docs.beam.cloud/v2/environment/jupyter-notebook.md)
- [Remote vs. Local Environment](https://docs.beam.cloud/v2/environment/remote-versus-local.md)
- [CPU and RAM](https://docs.beam.cloud/v2/environment/resources.md)
- [Storing Secrets](https://docs.beam.cloud/v2/environment/secrets.md): How to store secrets and environment variables in Beam
- [Serverless ComfyUI](https://docs.beam.cloud/v2/examples/comfy-ui.md)
- [Chat with DeepSeek R1](https://docs.beam.cloud/v2/examples/deepseek-r1.md)
- [Fine-tuning Gemma with LoRA](https://docs.beam.cloud/v2/examples/gemma-fine-tune.md)
- [Hugging Face Models](https://docs.beam.cloud/v2/examples/inference.md): A beginner's guide to running highly performant inference workloads on Beam.
- [LLaMA 3.1 8B](https://docs.beam.cloud/v2/examples/llama3.md)
- [Stable Diffusion with LoRAs](https://docs.beam.cloud/v2/examples/lora.md)
- [Text-to-Video with Mochi](https://docs.beam.cloud/v2/examples/mochi-1.md)
- [Parler TTS](https://docs.beam.cloud/v2/examples/parler-tts.md)
- [Qwen2.5-7B with SGLang](https://docs.beam.cloud/v2/examples/sglang.md)
- [Running Streamlit Apps](https://docs.beam.cloud/v2/examples/streamlit.md)
- [Fine-Tuning Meta Llama 3.1 8B with Unsloth](https://docs.beam.cloud/v2/examples/unsloth.md)
- [Run an OpenAI-Compatible vLLM Server](https://docs.beam.cloud/v2/examples/vllm.md)
- [Web Scraping with Beam Functions](https://docs.beam.cloud/v2/examples/web-scraping.md)
- [Faster Whisper](https://docs.beam.cloud/v2/examples/whisper.md)
- [Zonos](https://docs.beam.cloud/v2/examples/zonos.md)
- [Distributed Maps](https://docs.beam.cloud/v2/function/maps.md): Using Beam's distributed Map
- [Queues](https://docs.beam.cloud/v2/function/queues.md): Using Beam's distributed Queue to coordinate between tasks
- [Running Functions Remotely](https://docs.beam.cloud/v2/function/running-functions.md): A short guide on using Beam to run one-off functions in the cloud
- [Scheduled Jobs](https://docs.beam.cloud/v2/function/scheduled-job.md): How to run workloads on a schedule.
- [Core Concepts](https://docs.beam.cloud/v2/getting-started/core-concepts.md)
- [Installation](https://docs.beam.cloud/v2/getting-started/installation.md)
- [Introduction](https://docs.beam.cloud/v2/getting-started/introduction.md)
- [Quickstart](https://docs.beam.cloud/v2/getting-started/quickstart.md)
- [Networking](https://docs.beam.cloud/v2/pod/networking.md)
- [Host a Web Service](https://docs.beam.cloud/v2/pod/web-service.md)
- [Reference](https://docs.beam.cloud/v2/reference/api.md)
- [List Containers/Pods/Sandboxes](https://docs.beam.cloud/v2/reference/api-docs/gatewayservice/get-containers.md): List containers/pods/sandboxes associated with your workspace.
- [Stop Container/Pod/Sandbox](https://docs.beam.cloud/v2/reference/api-docs/gatewayservice/post-containers-stop.md): Stop a running container/pod/sandbox by its ID.
- [Create a Pod](https://docs.beam.cloud/v2/reference/api-docs/podservice/post-pods.md): Create a new pod and return its identifiers and initial state. Provide exactly one of `stubId` or `checkpointId`.
- [Cancel Task](https://docs.beam.cloud/v2/reference/api-docs/tasks/tasks-cancel.md)
- [Get Task Status](https://docs.beam.cloud/v2/reference/api-docs/tasks/tasks-status.md)
- [CLI Reference](https://docs.beam.cloud/v2/reference/cli.md)
- [Python SDK Reference](https://docs.beam.cloud/v2/reference/py-sdk.md)
- [TypeScript SDK Reference - Beta](https://docs.beam.cloud/v2/reference/ts-sdk.md)
- [FAQ](https://docs.beam.cloud/v2/resources/faq.md): This is an ongoing list of issues people sometimes encounter while using Beam. If you're having an issue, check this list first.
- [Pricing and Billing](https://docs.beam.cloud/v2/resources/pricing-and-billing.md)
- [Configuration](https://docs.beam.cloud/v2/sandbox/configuration.md): Learn how to configure and customize your sandbox environment
- [File System Operations](https://docs.beam.cloud/v2/sandbox/filesystem.md): Upload, download, and manage files within your sandbox environment
- [Networking](https://docs.beam.cloud/v2/sandbox/networking.md): Expose ports dynamically for services running inside your sandbox
- [Overview](https://docs.beam.cloud/v2/sandbox/overview.md): Run anything in secure code execution environments
- [Process Management](https://docs.beam.cloud/v2/sandbox/processes.md): Execute code and commands with real-time output streaming in your sandbox
- [Snapshots](https://docs.beam.cloud/v2/sandbox/snapshots.md)
- [Scaling Out](https://docs.beam.cloud/v2/scaling/concurrency.md)
- [Concurrent Inputs](https://docs.beam.cloud/v2/scaling/concurrent-inputs.md)
- [Parallelizing Functions](https://docs.beam.cloud/v2/scaling/parallelizing-functions.md): How to parallelize your functions
- [Privacy Policy](https://docs.beam.cloud/v2/security/privacy-policy.md)
- [Terms and Conditions](https://docs.beam.cloud/v2/security/terms-and-conditions.md): These are the terms the Beam Platform is provided under.
- [Amazon Web Services](https://docs.beam.cloud/v2/self-hosting/aws.md): Learn how to deploy Beam OSS (Beta9) to Amazon EKS.
- [Local Machine](https://docs.beam.cloud/v2/self-hosting/local-machine.md): Learn how to deploy Beam OSS (Beta9) to your local machine.
- [Overview](https://docs.beam.cloud/v2/self-hosting/overview.md): Beta9 is the open source project that powers Beam
- [Querying Task Status](https://docs.beam.cloud/v2/task-queue/query-status.md)
- [Running Async Tasks](https://docs.beam.cloud/v2/task-queue/running-tasks.md)
- [Task Callbacks](https://docs.beam.cloud/v2/topics/callbacks.md): Setup a callback to your server when a task finishes running
- [Integrate into CI/CD](https://docs.beam.cloud/v2/topics/ci.md): You can integrate Beam into an existing CI/CD process to deploy your code automatically.
- [Cold Start Performance](https://docs.beam.cloud/v2/topics/cold-start.md)
- [Runtime Variables](https://docs.beam.cloud/v2/topics/context.md): Accessing information about the runtime while running tasks
- [Public Endpoints](https://docs.beam.cloud/v2/topics/public-endpoints.md): Deploying public web endpoints on Beam
- [Send Events Between Apps](https://docs.beam.cloud/v2/topics/signal.md)
- [Timeouts and Retries](https://docs.beam.cloud/v2/topics/timeouts-and-retries.md)

# Installation

## Mac and Linux

This installs the Beam SDK and CLI in your Python environment.

```bash theme={null}
pip install beam-client
```

Beam will create a credentials file in `~/.beam/config.ini`. When you run `beam config create`, your API keys will be saved to this file.

## Homebrew

You can install the CLI separately from the SDK using Homebrew:

```bash theme={null}
brew tap beam-cloud/beam

brew install beam
```

## Windows

You can install Beam on Windows using [Windows Subsystem for Linux](https://learn.microsoft.com/en-us/windows/wsl/install) (WSL).

These steps assume you're starting fresh, but note that some systems (e.g. with Docker Desktop) may already have WSL distributions installed.

<Steps>
  <Step title="Install WSL with Ubuntu 22.04">
    After installation, you may be prompted to set up a new user for the Ubuntu
    environment: `wsl --install Ubuntu-22.04`
  </Step>

  <Step title="Set WSL Version to 1 (Optional)">
    Only do this if you explicitly need WSL 1. Most users should stick with WSL
    2: `wsl --set-version Ubuntu-22.04 1`
  </Step>

  <Step title="Launch Ubuntu">
    This ensures you’re using the correct distribution (not docker-desktop or
    others): `wsl -d Ubuntu-22.04`
  </Step>

  <Step title="Install pip">
    `sudo apt update && sudo apt install python3-pip -y`
  </Step>

<Step title="Install Beam SDK">`python3 -m pip install beam-client`</Step>
</Steps>

## Upgrading

Once installed, you can upgrade the CLI by running:

```bash theme={null}
python3 -m pip install --upgrade beam-client
```

## Uninstalling

The Python SDK can be uninstalled using `pip`:

```bash theme={null}
python3 -m pip uninstall beam-client
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Core Concepts

## How Beam Works

Beam is a new kind of cloud provider that makes the experience of using the cloud feel almost the same as using your local machine.

It's powered by an [open-source container orchestrator](https://github.com/beam-cloud/beta9) that launches containers in less than 1 second.

## Functions

You can run functions on the cloud, either once, or on a schedule. [Learn more about Functions](/v2/function/running-functions).

<CardGroup cols={2}>
  <Card title="Functions" icon="code">
    One-off Python functions, like training runs, scraping, or batch jobs.

    ```python  theme={null}
    from beam import function

    @function()
    def handler():
        return {}

    if __name__ == "__main__":
        # Runs locally
        handler.local()
        # Runs on the cloud
        handler.remote()
    ```

  </Card>

  <Card title="Scheduled Jobs" icon="clock">
    Functions that run based on a schedule you specify.

    ```python  theme={null}
    from beam import schedule

    @schedule(when="every 1d")
    def handler():
        return {}

    if __name__ == "__main__":
        # Runs locally
        handler.local()
        # Runs on the cloud
        handler.remote()
    ```

  </Card>
</CardGroup>

<CardGroup cols={1}>
  <Card title="Run Your Function" icon="bolt">
    You'll run your functions like a normal Python function: `python app.py`.
    Even though it *feels* like the code is running locally, it's running on a
    container in the cloud.
  </Card>
</CardGroup>

## Endpoints

You can also deploy synchronous and asynchronous web endpoints. Learn more about [Endpoints](/v2/endpoint/overview) and [Task Queues](/v2/task-queue/running-tasks).

<CardGroup cols={2}>
  <Card title="Endpoints" icon="bolt">
    Synchronous REST API endpoints, for tasks that run in 60s or less.

    ```python  theme={null}
    from beam import endpoint

    @endpoint(name="quickstart")
    def handler():
      return {}
    ```

  </Card>

  <Card title="Task Queues" icon="layer-group">
    Asynchronous REST API endpoints, for heavier tasks that take a long time to run.

    ```python  theme={null}
    from beam import task_queue

    @task_queue(name="quickstart")
    def handler():
      print(48393 * 39383)
    ```

  </Card>
</CardGroup>

<CardGroup cols={1}>
  <Card title="Testing Your Code (Optional)" icon="loader">
    Beam provides a temporary cloud environment to test your code.

    <br />

    These environments hot-reload with your code changes. You can test your workflow end-to-end before deploying to production.

    ```bash  theme={null}
    beam serve app.py:handler
    ```

  </Card>
</CardGroup>

<CardGroup cols={1}>
  <Card title="Deploying to Production" icon="check-double">
    When you're ready to deploy a persistent endpoint, you'll use `beam deploy`:

    ```bash  theme={null}
    beam deploy app.py:handler
    ```

  </Card>
</CardGroup>

## Web Services

You can also bring your own container and host web services, like Jupyter Notebooks, Node.js apps, and much more. [Learn more about Pods](/v2/pod/web-service).

<Card title="Pods" icon="bolt">
  Run any container behind an SSL-backed REST API.

```python theme={null}
from beam import Pod

pod = Pod(
  name="my-pod",
  cpu=2,
  memory="1Gi",
  ports=[8000],
  entrypoint=["python", "-m", "http.server", "--bind", "::", "8000"],
)

# Run the container as an API
pod.deploy()
```

</Card>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Container Images

Applications on Beam are run inside _containers_. A container is like a lightweight VM that packages a set of software packages required by your application. The benefit of using containers is portability. The required runtime environment is packaged alongside the application.

Containers are based on container _images_ which are instructions for how a container should be built.

Because you are building a custom application, it is likely that your application depends on some custom software to run. This could include custom python packages, libraries, binaries, and drivers.

You can customize the container image used to run your Beam application with the [`Image`](/v2/reference/sdk#image) class. The options specified in the `Image` class will influence how the image is built.

## Exploring the Beam Image Class

Every application that runs on Beam instantiates the [`Image`](/v2/reference/sdk#image) class. This class provides a variety of methods for customizing the container image used to run your application.

It exposes options for:

- Installing a specific version of Python
- Adding custom shell commands that run during the build process
- Adding custom Python packages to install in the container
- Choosing a custom base image to build on top of
- Using a custom Dockerfile to build your own base image
- Setting up a custom conda environment using micromamba

<Tip>
  The default Beam image uses `ubuntu:22.04` as its base and installs Python
  3.10.
</Tip>

```python theme={null}
from beam import function, Image

image = Image()

# This function will use ubuntu:22.04 with Python 3.10
@function(image=image)
def hello_world():
    return "Hello, world!"

hello_world.remote()
```

## Adding Python Packages

The most common way to customize your image is to add the Python packages required by your application. This is done by calling the `add_python_packages` method on the `Image` object with a list of package names.

<Tip>
  Pinning the version of the package is recommended. This ensures that when you
  re-deploy your application, you won't accidentally pick up a new version that
  breaks your application.
</Tip>

```python theme={null}
from beam import Image, endpoint

image = Image(python_version="python3.11").add_python_packages(["numpy==2.2.0"])

@endpoint(image=image)
def handler():
  return {}
```

### Importing `requirements.txt`

If you already have a `requirements.txt` file, you can also use that directly using the `Image` constructor's `python_packages` parameter:

```python theme={null}
from beam import Image, endpoint

image = Image(python_version="python3.11", python_packages="requirements.txt")

@endpoint(image=image)
def handler():
  return {}
```

## Adding Shell Commands

Sometimes, it is necessary to run additional shell commands while building your image. This can be achieved by calling the `add_commands` method on the `Image` object with a list of commands.

For instance, you might need to install `libjpeg-dev` when using the `Pillow` library. In the example below, we'll install `libjpeg-dev` and then install `Pillow`.

```python theme={null}
from beam import Image, endpoint

image = (
    Image(python_version="python3.11")
    .add_commands(["apt-get update", "apt-get install libjpeg-dev -y"])
    .add_python_packages(["Pillow"])
)

@endpoint(image=image)
def handler():
  return {}
```

## Customizing the Base Image

Some applications and libraries require specific dependencies that are not available in the default Beam image. In these cases, you can use a custom base image.

Some of the most common custom base images are the CUDA development images from NVIDIA (e.g. `nvcr.io/nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04`). These images come with additional libraries, debugging tools, and `nvcc` installed.

The image below will use a custom CUDA image as the base.

```python theme={null}
from beam import Image, function

image = Image(
    base_image="nvcr.io/nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04"
)

@function(image=image)
def hello_world():
    return "Hello, world!"

hello_world.remote()
```

### CUDA Drivers & NVIDIA Kernel Drivers

When choosing a custom base image, it is important to understand the difference between the NVIDIA Kernel Driver and the CUDA Runtime & Libraries.

| **Component**                | **Location**     | **Role**                                                 |
| ---------------------------- | ---------------- | -------------------------------------------------------- |
| **NVIDIA Kernel Driver**     | **Host Machine** | Low-level GPU management, talks directly to hardware.    |
| **CUDA Runtime & Libraries** | **Container**    | Provides high-level APIs and libraries for applications. |

The NVIDIA Kernel Driver on the host must support the CUDA version used by the container.

In general, if the CUDA version on the host is greater than or equal to the CUDA version in the container, then the NVIDIA Kernel Driver on the host will support the CUDA version used by the container.

<Tip>
  For example, using a CUDA 12.2 image on a host with a CUDA 12.4 driver will
  work. However, using a CUDA 12.8 image on a host with a CUDA 12.4 driver *will
  not* work.
</Tip>

You can consult the table below to help you choose a compatible base image.

| GPU     | Driver Version | CUDA Version |
| ------- | -------------- | ------------ |
| A10G    | 550.90.12      | 12.4         |
| RTX4090 | 550.127.05     | 12.4         |
| H100    | 550.127.05     | 12.4         |

## Using a Specific Python Version

To install a specific version of Python, you can use the `python_version` parameter:

```python theme={null}
from beam import function, Image


# This function will use ubuntu:22.04 with Python 3.11
@function(image=Image(python_version="python3.11"))
def hello_world():
    return "Hello, world!"

hello_world.remote()
```

This function will use the CUDA image as the base and install Python 3.10 because no `python_version` is specified and the CUDA image has no Python version installed.

```python theme={null}
from beam import Image, function


@function(
    image=Image(
        base_image="nvcr.io/nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04",
    )
)
def custom_image_no_python():
    return "Hello, world!"
```

This function will use the CUDA image as the base and install Python 3.11 because a `python_version` _is_ specified.

```python theme={null}
from beam import Image, function


@function(
    image=Image(
        base_image="nvcr.io/nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04",
        python_version="python3.11",
    )
)
def custom_image_python_requested():
    return "Hello, world!"
```

If your image comes with a pre-installed version of Python3, it will be used by default _as long as_ you don't specify a `python_version` in your `Image` constructor. This function will use the PyTorch image as the base and will use the Python version that already exists in the PyTorch image.

```python theme={null}
from beam import Image, function


@function(
    image=Image(
        base_image="docker.io/pytorch/pytorch:2.2.1-cuda12.1-cudnn8-devel"
    )
)
def custom_image_pytorch():
    return "Hello, world!"
```

## Building on GPU

By default, Beam builds your images on CPU-only machines. However, sometimes you might need the build to occur on a machine with a GPU.

For instance, some libraries might compile CUDA kernels during installation. In these cases, you can use the `build_with_gpu()` command to run your build on the GPU of your choice.

```python theme={null}
from beam import Image

image = (
    Image()
    .add_commands(
        [
            "apt-get update -y",
            "apt-get install ffmpeg -y",
            "apt-get install nvidia-cuda-toolkit -y", # Requires GPU to install
        ]
    )
    .build_with_gpu(gpu="T4") # Install on a T4
)
```

## Building with Environment Variables

Often, shell commands require certain environment variables to be set. You can set these using the `with_envs` command:

```python theme={null}
from beam import Image

image = (
    Image()
    .add_python_packages(["huggingface_hub[cli]", "accelerate"])
    .with_envs(["HF_HUB_ENABLE_HF_TRANSFER=1", "HF_HOME"=/models])
    .add_commands(["huggingface-cli download meta-llama/Llama-3.2-3B"])
)
```

### Injecting Secrets

Sometimes, you might not want the environment variables to be set in plain text. In these cases, you can leverage Beam secrets and the `with_secrets` command:

<Tip>
  You can create secrets like this, using the CLI: `beam secret create HF_TOKEN <your_token>`.
</Tip>

```python theme={null}
from beam import Image

image = (
    Image()
    .add_python_packages(["huggingface_hub[cli]", "accelerate"])
    .with_envs(["HF_HUB_ENABLE_HF_TRANSFER=1", "HF_HOME"=/models])
    .with_secrets(["HF_TOKEN"]) # Models with a user agreement often require a token
    .add_commands(["huggingface-cli download meta-llama/Llama-3.2-3B"])
)
```

**Note** Adding secrets and environment variables to the build environment _does not_ make them available in the runtime environment.

Runtime environment variables and secrets must be specified in the function decorator directy:

```python theme={null}
from beam import function

@function(env_vars={"HF_HOME": "/models"}, secrets=["HF_TOKEN"])
def download_model():
    return "Hello, world!"
```

## Using a Dockerfile

You also have the option to build your own custom base image using a Dockerfile.

The `from_dockerfile()` command accepts a path to a valid Dockerfile as well as an optional path to a context directory:

```python theme={null}
from beam import Image, endpoint

image = Image().from_dockerfile("./Dockerfile").add_python_packages(["numpy"])


@endpoint(image=image, name="test_dockerfile")
def handler():
  return {}
```

The context directory serves as the root for any paths used in commands like `COPY` and `ADD`, meaning all relative paths are relative to this directory.

The image built from your Dockerfile will be used as the base image for a Beam application.

<Info>
  Ports *will not* be exposed in the runtime environment, and the entrypoint
  will be overridden.
</Info>

## Conda Environments

Beam supports using Anaconda environments via [micromamba](https://mamba.readthedocs.io/en/latest/user_guide/micromamba.html). To get started, you can chain the `micromamba` method to your `Image` definition and then specify packages and channels via the `add_micromamba_packages` method.

```python theme={null}
from beam import Image


image = (
    Image(python_version="python3.11")
    .micromamba()
    .add_micromamba_packages(packages=["pandas", "numpy"], channels=["conda-forge"])
    .add_python_packages(packages=["huggingface-hub[cli]"])
    .add_commands(commands=["micromamba run -n beta9 huggingface-cli download gpt2 config.json"])
)
```

You can still use `pip` to install additional packages in the `conda` environment and you can run shell commands too.

<Tip>
  If you need to run a shell command inside the conda environment, you should
  prepend the command with `micromamba run -n beta9` as shown above.
</Tip>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# GPU Acceleration

## Running Tasks on GPU

You can run any code on a cloud GPU by passing a `gpu` argument in your function decorator.

```python theme={null}
from beam import endpoint


@endpoint(gpu="H100")
def handler():
    # Prints the available GPU drivers
    import subprocess
    print(subprocess.check_output(["nvidia-smi"], shell=True))

    return {"gpu":"true"}
```

### Available GPUs

Currently available GPU options are:

- `A10G` (24Gi)
- `RTX4090` (24Gi)
- `H100` (80Gi)

### Check GPU Availability

Run `beam machine list` to check whether a machine is available.

```bash theme={null}
$ beam machine list

  GPU Type   Available
 ──────────────────────
  A10G          ✅
  RTX4090       ✅
```

## Prioritizing GPU Types

You can split traffic across multiple GPUs by passing a list to the `gpu` parameter.

The list is ordered by priority. You can choose which GPUs to prioritize by specifying them at the front of the list.

```python theme={null}
gpu=["T4", "A10G", "H100"]
```

In this example, the `T4` is prioritized over the `A10G`, followed by the `H100`.

## Using Multiple GPUs

You can run workloads across multiple GPUs by using the `gpu_count` parameter.

<Warning>
  This feature is available *by request only*. Please send us a message in
  Slack, and we'll enable it on your account.
</Warning>

```python theme={null}
from beam import endpoint


@endpoint(gpu="A10G", gpu_count=2)
def handler():
    return {"hello": "world"}
```

## GPU Regions

Beam runs on servers distributed around the world, with primary locations in the United States, Europe, and Asia. If you would like your workloads to run in a specific region of the globe, [please reach out](https://join.slack.com/t/beam-cloud/shared_invite/zt-3enuvj3r7-OeAzVPYvyqQHy9avNrLL0w).

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# CPU and RAM

## Configuring CPU and Memory

In addition to choosing a GPU, you can choose the amount of CPU and Memory to allocate:

```python theme={null}
from beam import function

@function(cpu=2, memory="2Gi")
def some_function():
    pass
```

_GPU graphics cards_ have VRAM and run on _servers_ with RAM.

### RAM vs. VRAM

VRAM is the amount of memory available on the GPU device. For example, if you are running inference on a 13B parameter LLM, you'll usually need at least 40Gi of VRAM in order for the model to be loaded onto the GPU.

In contrast, RAM is responsible for the _amount of data_ that can be stored and accessed by the CPU on the server. For example, if you try downloading a 20Gi file, you'll need sufficient disk space and RAM.

In the context of LLMs, here are some approximate guidelines for resources to use in your apps:

| LLM Parameters | Recommended CPU | Recommended Memory (RAM) | Recommended GPU  |
| -------------- | --------------- | ------------------------ | ---------------- |
| 0-7B           | 2               | 32Gi                     | A10G (24Gi VRAM) |
| 7-14B+         | 4               | 32Gi                     | H100 (80Gi VRAM) |

### Monitoring Resource Usage

In the web dashboard, you can monitor the amount of CPU, Memory, and GPU memory used for your tasks.

On a deployment, click the `Metrics` button.

<Frame>
  <img src="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=892838046664b631fed15fb59e169347" data-og-width="1489" width="1489" data-og-height="393" height="393" data-path="img/v2/metrics-page.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?w=280&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=e1adc49baaffd9a6eba1b2cb34bd7b4f 280w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?w=560&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=7f5196b3f6e70fd5c7488a4db155a3d8 560w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?w=840&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=e502ebddabf9e1038b58853d38605ebc 840w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?w=1100&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=ebfa2cbec88bcec3af3abadcec7e98c5 1100w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?w=1650&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=94fe386ed1795428d7984cf7c979faad 1650w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/metrics-page.png?w=2500&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=9806de751125b99029caae994033743a 2500w" />
</Frame>

On this page, you can see the resource usage over time. The graph will also show the periods when your resource usage exceeded the resource limits set on your app:

<Frame>
  <img src="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=953880a5b2fa49c8894e0d9ff2aa2e96" data-og-width="2112" width="2112" data-og-height="718" height="718" data-path="img/v2/resource-usage.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?w=280&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=96c63f133dc8d1045ce6da4e4592c8b0 280w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?w=560&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=decd8ac76a1d97fddcdd424d971f897c 560w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?w=840&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=b1aff7205cf86c295ce218aa4371fed4 840w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?w=1100&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=a61089b4af6029f82a2bdfb743c9c5b1 1100w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?w=1650&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=72053107ebc06008ace7e02b7e643748 1650w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/resource-usage.png?w=2500&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=33e19241904d690ff3df74d50e7c9ba6 2500w" />
</Frame>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Storing Secrets

> How to store secrets and environment variables in Beam

### Storing Secrets and Environment Variables

Secrets and environment variables can be injected into the containers that run your apps.

You can manage secrets through the CLI:

```bash theme={null}
$ beam secret create AWS_ACCESS_KEY ASIAY34FZKBOKMUTVV7A

=> Created secret with name: 'AWS_ACCESS_KEY'
```

### Using Secrets

Once created, you can access a secret like an environment variable:

```python theme={null}
from beam import function


@function(secrets=["AWS_ACCESS_KEY"])
def handler():
    import os

    my_secret = os.environ["AWS_ACCESS_KEY"]
    print(f"Secret: {my_secret}")
```

### Passing Secrets to `on_start`

If your app used an `on_start` function, secrets can be passed to that function as well.

```python theme={null}
from beam import endpoint


# This has access to secrets passed down in the handler
def load_models():
    import os

    my_secret = os.environ["AWS_ACCESS_KEY"]
    print("The function can read secrets:", my_secret)


@endpoint(
    secrets=["AWS_ACCESS_KEY"],
    on_start=load_models,
)
def handler(context):
    return {}
```

## CLI Commands

### List Secrets

```bash theme={null}
beam secret list
```

```bash theme={null}
$ beam secret list

  Name             Last Updated     Created
 ──────────────────────────────────────────────────
  AWS_KEY          19 hours ago     19 hours ago
  AWS_ACCESS_KEY   20 seconds ago   20 seconds ago
  AWS_REGION       7 seconds ago    7 seconds ago

  3 items
```

### Create a Secret

```bash theme={null}
beam secret create [KEY] [VALUE]
```

```bash theme={null}
$ beam secret create AWS_ACCESS_KEY ASIAY34FZKBOKMUTVV7A

=> Created secret with name: 'AWS_ACCESS_KEY'
```

<Warning>
  If your secret contains special characters, you may need to escape them with a
  backslash. For example, `a$b` would need to be `a\$b`.
</Warning>

### Show a Secret

```bash theme={null}
beam secret create show [KEY]
```

```bash theme={null}
$ beam secret show AWS_ACCESS_KEY

=> Secret 'AWS_ACCESS_KEY': ASIAY34FZKBOKMUTVV7A
```

### Modify a Secret

```bash theme={null}
beam secret modify [KEY] [VALUE]
```

```bash theme={null}
$ beam secret modify AWS_ACCESS_KEY ASIAY34FZKBOKMUTVV7A

=> Modified secret 'AWS_ACCESS_KEY'
```

### Delete a Secret

```bash theme={null}
beam secret delete [KEY]
```

```bash theme={null}
$ beam secret delete AWS_ACCESS_KEY

=> Deleted secret 'AWS_ACCESS_KEY'
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Ephemeral Files and Images

> Storing ephemeral files for images, audio files, and more.

You may want to save data produced by your tasks. Beam provides an abstraction called `Output`, which allows you to save files or directories and generate public URLs to access them.

## Saving Files

To save an `Output`, you can write any filetype to Beam's `/tmp` directory.

Here's what your code might look like:

```python theme={null}
from beam import function, Output


@function()
def save_output():
    # File is saved to /tmp directory
    file_name = "/tmp/my_output.txt"

    # Write to new text file
    with open(file_name, "w") as f:
        f.write("This is an output, a glorious text file.")

    # Save output
    output_file = Output(path=file_name)
    output_file.save()

    # Generate and return a public URL
    public_url = output_file.public_url(expires=400)
    return {"output_url": public_url}
```

### Directories

You can also create public URLs for directories, by passing in a directory path:

```python theme={null}
# Generate a public URL for a directory
file_path = "./tmp/waveforms"
output = Output(path=file_path)
output.save()

# Returns https://app.beam.cloud/output/id/abe0c95a-2cd1-40b3-bace-9225f2c79c6d
output_url = output.public_url()
```

### PIL Images

If your app uses PIL, `Output` includes a wrapper around PIL to simplify the process of generating a public URL for the PIL image file:

```python theme={null}
# Save a PIL image
image = pipe(...)

# Persist the PIL image to an Output
output = Output.from_pil_image(image).save()
```

Here's a full example:

```python theme={null}
from beam import Image as BeamImage, Output, function


@function(
    image=BeamImage(
        python_packages=[
            "pillow",
        ],
    ),
)
def save_image():
    from PIL import Image as PILImage

    # Generate PIL image
    pil_image = PILImage.new(
        "RGB", (100, 100), color="white"
    )  # Creating a 100x100 white image

    # Save image file
    output = Output.from_pil_image(pil_image)
    output.save()

    # Retrieve pre-signed URL for output file
    url = output.public_url(expires=400)
    print(url)

    # Print other details about the output
    print(f"Output ID: {output.id}")
    print(f"Output Path: {output.path}")
    print(f"Output Stats: {output.stat()}")
    print(f"Output Exists: {output.exists()}")

    return {"image": url}


if __name__ == "__main__":
    save_image()
```

When you run this function, it will return a pre-signed URL to the image:

```bash theme={null}
https://app.beam.cloud/output/id/abe0c95a-2cd1-40b3-bace-9225f2c79c6d
```

## Generating Public URLs

Your app might return files from the API, such as images or MP3s. You can use `Output` to generate a public URL to access the content.

<Frame>
  <img src="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=93bf6bd0d3dd58537935d18e43ee81ab" data-og-width="1092" width="1092" data-og-height="706" height="706" data-path="img/v2/output-graphic.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?w=280&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=2bbb988833247713bddc00f955712fe1 280w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?w=560&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=456565ca7e0f831cbc16f8c62ea2f0aa 560w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?w=840&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=65ff37389517e1bbbe4996b562935be0 840w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?w=1100&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=b3ddb7e94301210a84627fd1435ebaa0 1100w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?w=1650&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=703c3309b678c07d5222405ba2d0d4e2 1650w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/output-graphic.png?w=2500&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=14128cd07d7b763f131d9192f2b75a16 2500w" />
</Frame>

### Expiring Public URLs

You can pass an optional `expires` parameter to `output.public_url` to control how long to persist the file before it is deleted.

<Info>By default, public URLs are automatically deleted after 1 hour.</Info>

```python theme={null}
# Delete public URL after 5 minutes
output.public_url(expires=300)
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Parallelizing Functions

> How to parallelize your functions

## Fanning Out Workloads

You can scale out individual Python functions to many containers using the `.map()` method.

You might use this for parallelizing computational-heavy tasks, such as batch inference or data processing jobs.

```python theme={null}
from beam import function


@function(cpu=0.1)
def square(i: int):
    return i**2


def main():
    numbers = list(range(10))
    squared = []

    # Run a remote container for every item in list
    for result in square.map(numbers):
        print(result)
        squared.append(result)


if __name__ == "__main__":
    main()
```

When we run this Python module, 10 containers will be spawned to run the workload:

```bash theme={null}
$ python math-app.py

=> Building image
=> Using cached image
=> Syncing files

=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>
=> Running function: <map-example:square>

=> Function complete <a6a1c063-b0d7-4c62-b6b1-a7940b19fde9>
=> Function complete <531e1f2c-a4f2-4edf-9cb9-6240df959815>
=> Function complete <bc421f5a-e09b-42d4-8035-d3d13ca5c238>
=> Function complete <2a3dde03-20df-4805-8fb0-ec9743f2bde3>
=> Function complete <59b64517-7b4a-4260-8c65-d0fbb9b98a76>
=> Function complete <f0ab7790-e2fb-441f-8278-74856719a457>
=> Function complete <1256a9ac-c035-412a-ac65-c94248f1ce99>
=> Function complete <476189dd-ba28-4646-9911-96ef8794cb58>
=> Function complete <04ef44cd-ff64-4ef2-a087-00c01ce5a2e4>
=> Function complete <104a602c-93a7-43d5-983c-071f64d91a2c>
```

## Passing Multiple Arguments

The `.map()` method can also parallelize functions that require multiple parameters. Simply pass a list of tuples, where each tuple corresponds to a set of arguments for your function.

Below is an example that counts how many prime numbers appear between a start and a stop index for each tuple in ranges:

```python theme={null}
from beam import function

def is_prime(n: int) -> bool:
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True

@function(cpu=0.1)
def count_primes_in_range(start: int, stop: int) -> int:
    """
    Returns the number of prime numbers in the range [start, stop).
    """
    return sum(is_prime(i) for i in range(start, stop))

def main():
    # Each tuple represents (start, stop)
    ranges = [
        (0, 10),
        (10, 20),
        (20, 30)
    ]

    # .map() will launch a remote container for each tuple
    for result in count_primes_in_range.map(ranges):
        print(result)

if __name__ == "__main__":
    main()
```

In this example:

1. `ranges` is a list of tuples `(start, stop)`.
2. Calling `count_primes_in_range.map(ranges)` spawns a remote execution for each tuple, passing `(start, stop)` to the function.
3. Each remote call returns the number of prime numbers in that sub-range, which we print out.

With `.map()`, Beam takes care of distributing each item (or tuple of items) to separate containers for parallel processing. This approach makes it easy to scale out CPU-heavy or data-intensive tasks with minimal code.

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Creating a Web Endpoint

> Deploying and invoking web endpoints on Beam

Beam allows you to deploy web endpoints that can be invoked via HTTP requests. These endpoints can be used to run arbitrary code. For instance, you could perform inference using one of our GPUs, or just run a simple function that multiplies two numbers.

```python theme={null}
from beam import endpoint

@endpoint(
    cpu=1.0,
    memory=128,
)
def multiply(**inputs):
    result = inputs["x"] * 2
    return {"result": result}
```

<Tip>
  **Endpoints vs. Task Queues**

Endpoints are RESTful APIs, designed for synchronous tasks that can complete in 180 seconds or less. For longer running tasks, you'll want to use an asynchronous [`task_queue`](/v2/task-queue/running-tasks) instead.
</Tip>

#### Launch a Preview Environment (Optional)

[`beam serve`](/getting-started/cli#serve) monitors changes in your local file system, live-reloads the remote environment as you work, and forwards remote container logs to your local shell.

Serve is great for prototyping. You can develop in a containerized cloud environment in real-time, with adjustable CPU, memory, GPU resources.

It's also great for testing an app before deploying it. Served functions are orchestrated identically to deployments, which means you can test your Beam workflow end-to-end before deploying.

To start an ephemeral `serve` session, you'll use the `serve` command:

```sh theme={null}
beam serve [FILE.PY]:[ENTRY-POINT]
```

For example, to start a session for the `multiply` function in `app.py`, run:

```sh theme={null}
beam serve app.py:multiply
```

To end the session, you can use `Ctrl + C` in the terminal where you started the session.

<Warning>
  Serve sessions end automatically after 10 minutes of inactivity. The entire
  duration of the session is counted towards billable usage, even if the session
  is not receiving requests.
</Warning>

<Tip>
  By default, Beam will sync all the files in your working directory to the
  remote container. This allows you to use the files you have locally while
  developing. If you want to prevent some files from getting uploaded, you can
  create a [`.beamignore`](/getting-started/cli#ignore-local-files).
</Tip>

### Deploying the Endpoint

When you're finished with prototyping and want to make a persistent deployment of the endpoint, enter your shell and run this command from the working directory:

```bash theme={null}
beam deploy [FILE.PY]:[ENTRY-POINT]
```

After running this command, you'll see some logs in the console that show the progress of your deployment.

<Accordion title="Show Logs">
  ```bash  theme={null}
  $ beam deploy app.py:multiply

=> Building image
=> Using cached image
=> Syncing files
Reading .beamignore file
=> Files synced
=> Deploying endpoint
=> Deployed 🎉
=> Invocation details

curl -X POST 'https://multiply-712408b-v1.app.beam.cloud' \
 -H 'Accept: _/_' \
 -H 'Accept-Encoding: gzip, deflate' \
 -H 'Connection: keep-alive' \
 -H 'Authorization: Bearer [YOUR_AUTH_TOKEN]' \
 -H 'Content-Type: application/json' \
 -d '{}'

````
</Accordion>

<Info>
The container handling the endpoint will spin down after 180 seconds of inactivity by default, or customized with the `keep_warm_seconds` parameter. The container will be billed for the time it is active and handling requests.
</Info>

### Calling the Endpoint

After deploying the API, you'll be able to make a web request to hit the API with cURL or libraries of your choice.

<Tabs>
<Tab title="cURL">
  Open another terminal window to invoke the API:

  ### Example Request

  ```sh  theme={null}
  curl -X POST 'https://multiply-712408b-v1.app.beam.cloud' \
  -H 'Accept: */*' \
  -H 'Accept-Encoding: gzip, deflate' \
  -H 'Connection: keep-alive' \
  -H 'Authorization: Bearer [YOUR_AUTH_TOKEN]' \
  -H 'Content-Type: application/json' \
  -d '{"x": 10}'
  ```

  ### Example Response

  ```json  theme={null}
  {
    "result": 20
  }
  ```
</Tab>

<Tab title="Python">
  In Python, you can use the `requests` library to make a POST request to the endpoint:

  ```python  theme={null}
  import requests

  url = "https://multiply-712408b-v1.app.beam.cloud"
  headers = {
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Authorization": "Bearer [YOUR_AUTH_TOKEN]",
  }
  data = {"x": 10}

  response = requests.post(url, headers=headers, json=data)
  print(response.json())
  ```

  ### Example Response

  ```json  theme={null}
  { "result": 20 }
  ```
</Tab>
</Tabs>

To send other payloads other than JSON, you can encode the data as a base64 string and include it in the JSON payload, or upload the file to a S3 bucket and mount the bucket to the endpoint.

For more detailed examples, checkout the [Sending File Payloads](/v2/endpoint/sending-file-payloads) documentation.

````

````


---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Realtime and Streaming

## Deploying a Realtime App

This is a simple example of a realtime streaming app. When deployed, this app will be exposed as a public websocket endpoint.

The `realtime` handler accepts a single parameter, called `event`, with the event payload.

<Tip>
  The `realtime` decorator is an abstraction above `asgi`.

  This means that additional parameters in `asgi`, such as [`concurrent_requests`](/v2/endpoint/web-server#concurrent-requests) can be used too.
</Tip>

```python app.py theme={null}
from beam import realtime


@realtime(
    cpu=1,
    memory="1Gi",
    concurrent_requests=10, # Process 10 requests at a time
    authorized=False, # Don't require auth to invoke
)
def stream(event):
    # Echo back the event payload sent to the websocket
    return {"response": event}
````

This app can be deployed in traditional Beam fashion:

```sh theme={null}
beam deploy app.py:stream
```

## Streaming Responses from the Client

Realtime Endpoints can be connected to from any websocket client.

<Tabs>
  <Tab title="Beam Javascript SDK">
    The code below uses the Beam Javascript SDK to send requests to the realtime app.

    Make sure to add an `.env` file to your project with your `BEAM_DEPLOYMENT_ID` and `BEAM_TOKEN`:

    ```javascript client.js theme={null}
    import beam from "@beamcloud/beam-js";

    const streamResponse = async () => {
      const client = await beam.init(process.env.BEAM_TOKEN);
      const deployment = await client.deployments.get({ id: process.env.BEAM_DEPLOYMENT_ID });

      const connection = await deployment.realtime();

      const payload = {
        "event": "Echo this back",
      }

      connection.onmessage = (message) => {
          console.log(`🎉 Response: ${message.data}`);
      };

      connection.send(JSON.stringify(payload));

      setTimeout(() => {
        connection.close();
      }, 1000);
    };

    streamResponse();
    ```

  </Tab>

  <Tab title="Javascript">
    The code below uses the native WebSocket API to send requests to the realtime app.

    ```javascript client.js theme={null}
    const socket = new WebSocket("wss://1c0f0cbe-e0d1-49ae-a556-5daffe23eb4c.app.beam.cloud");

    // Connection opened
    socket.addEventListener("open", (event) => {
      socket.send("Hello Server!");
    });

    // Listen for messages
    socket.addEventListener("message", (event) => {
      console.log(event.data); // {"response":"Hello Server!"}
    });
    ```

  </Tab>
</Tabs>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Pre-Loading Models

> This guide shows how you can optimize performance by pre-loading models when your container first starts.

Beam includes an optional `on_start` lifecycle hook which you can add to your functions. The `on_start` function will be run exactly once when your container first starts.

```python app.py theme={null}
from beam import endpoint


def download_models():
    # Do something that only needs to happen once
    return {}


# The on_start function runs once when the container starts
@endpoint(on_start=download_models)
def handler():
    return {}
```

Anything returned from `on_start` can be retrieved in the `context` variable that is automatically passed to your handler:

```python theme={null}
from beam import endpoint


def download_models():
    # Do something that only needs to happen once
    x = 10
    return {"x": x}


# The on_start function runs once when the container starts
@endpoint(on_start=download_models)
def handler(context):
    # Retrieve cached values from on_start
    on_start_value = context.on_start_value
    return {}
```

# Example: Downloading Model Weights

```python theme={null}
from beam import Image, endpoint, Volume


CACHE_PATH = "./weights"


def download_models():
    from transformers import AutoTokenizer, OPTForCausalLM

    model = OPTForCausalLM.from_pretrained("facebook/opt-125m", cache_dir=CACHE_PATH)
    tokenizer = AutoTokenizer.from_pretrained("facebook/opt-125m", cache_dir=CACHE_PATH)

    return model, tokenizer


@endpoint(
    on_start=download_models,
    volumes=[Volume(name="weights", mount_path=CACHE_PATH)],
    cpu=1,
    memory="16Gi",
    gpu="T4",
    image=Image(
        python_version="python3.8",
        python_packages=[
            "transformers",
            "torch",
        ],
    ),
)
def predict(context, prompt):
    # Retrieve cached model from on_start function
    model, tokenizer = context.on_start_value

    # Generate
    inputs = tokenizer(prompt, return_tensors="pt")
    generate_ids = model.generate(inputs.input_ids, max_length=30)
    result = tokenizer.batch_decode(
        generate_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]

    print(result)

    return {"prediction": result}
```

## Using Loaders with Multiple Workers

<Tip>
  If you are scaling out vertically with
  [workers](/v2/scaling/concurrency#increasing-throughput-in-a-single-container),
  the loader function will run once for each worker that starts up.
</Tip>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Keeping Containers Warm

> Control how long your apps stay running before shutting down.

By default, Beam is serverless, which means your applications will shut off automatically when they're not being used.

## Configuring Keep Warm

You can control how long your containers are kept alive by using the `keep_warm_seconds` flag in your deployment trigger.

For example, by adding a `keep_warm_seconds=300` argument to an endpoint, your app will stay running for 5 minutes before shutting off:

```python theme={null}
from beam import endpoint


# Container stays alive for 5 min before shutting down automatically
@endpoint(keep_warm_seconds=300)
def handler():
    return {}
```

<Warning>
  When `keep_warm_seconds` is set in your deployment, it will count as billable
  usage.
</Warning>

## Setting Always-On Containers

<Note>
  Any running containers count towards billable usage. Take care to avoid
  setting `min_containers` unless you're comfortable paying for usage 24/7.
</Note>

You can configure the number of containers running at baseline using the `min_containers` field.

By setting `min_containers=1`, 1 container will _always_ remain running until the deployment is stopped.

<Warning>
  If you redeploy an app that has `min_containers` set, make sure to explicitly
  stop the previous deployment versions in order to avoid running containers
  that you are no longer using.
</Warning>

```python theme={null}
from beam import endpoint, QueueDepthAutoscaler


@endpoint(
    autoscaler=QueueDepthAutoscaler(
        min_containers=1, max_containers=3, tasks_per_container=1
    ),
)
def handler():
    return {"success": "true"}
```

## Pre-Warming Your Container

You can pre-warm your containers by adding `/warmup` to the end of your deployment URL:

```sh theme={null}
curl -X POST 'https://hello-world-a4bdc39-v1.app.beam.cloud/warmup' \
     -H 'Authorization: Bearer [YOUR_TOKEN]'
```

When invoked, this endpoint will send a request to the container to warm-up.

You can add `/warmup` to the end of any of your deployment APIs to warm-up your container:

```
id/:stubId/warmup
/:deploymentName/warmup
/:deploymentName/latest/warmup
/:deploymentName/v:version/warmup
```

## Default Container Spin-down Times

After handling a request, Beam keeps containers running ("warm") for a certain amount of time in order to quickly handle future requests. By default, these are the container "keep warm" times for each deployment type:

| Deployment Type         | Container Keep Warm Duration |
| ----------------------- | ---------------------------- |
| Endpoints/ASGI/Realtime | 180s                         |
| Task Queues             | 10s                          |
| Pods                    | 600s                         |

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Sending File Payloads

> Sending file payloads to Endpoints and Web Servers

There are two easy ways to send files to your Beam endpoints and ASGI web servers.

## Sending Files to Endpoints Using Base64

The simplest way to send files to your Beam endpoint is to use Base64 encoding. In the example below, we will use this method to send an image to an endpoint. The first step is to define an endpoint that accepts an encoded string.

```python theme={null}
import base64
import io
from beam import endpoint
from beam import Image as BeamImage
from PIL import Image

@endpoint(name="image_endpoint", image=BeamImage().add_python_packages(["pillow"]))
def image_endpoint(image: str):
    image = base64.b64decode(image)
    image = Image.open(io.BytesIO(image))
    # do something with the image
    return {"message": "Image processed successfully"}
```

We can then deploy our endpoint with the command `beam deploy app.py:image_endpoint`. The simple script below can be used to send an image to the endpoint.

<CodeGroup>
  ```python Python theme={null}
  import base64
  import requests

with open("./cool-picture.png", "rb") as image_file:
encoded_string = base64.b64encode(image_file.read())
b64_image = encoded_string.decode("utf-8")

url = "https://image-endpoint-53b4230-v1.app.beam.cloud"
headers = {
"Connection": "keep-alive",
"Content-Type": "application/json",
"Authorization": "Bearer <your-token>",
}
data = {"image": b64_image}

response = requests.post(url, headers=headers, json=data)

````

```bash Curl theme={null}
export B64_FILE=$(base64 -i ./cool-picture.png)
curl -X POST "https://image-endpoint-53b4230-v1.app.beam.cloud" \
-H 'Connection: keep-alive' \
-H 'Content-Type: application/json' \
-H 'Authorization: Bearer <your-token>' \
-d '{"image": "$B64_FILE"}'
````

</CodeGroup>

## Using S3 to Send Files

With Beam, you can easily [mount S3 buckets](/v2/data/external-storage) to your endpoints and web servers. This allows you to upload files to S3 and access them in your endpoint or web server. This method is recommended if you are sending large payloads (20+ MB). Another benefit of using S3 is that you will not need to include decoding logic in your endpoint.

We can modify our previous example by accepting a filename and reading the image from a mounted S3 bucket. Our frontend will need to upload the image to the S3 bucket and then pass the filename to our endpoint.

```python theme={null}
import os
from beam import CloudBucket, CloudBucketConfig, endpoint
from beam import Image as BeamImage
from PIL import Image

mount_path = "./uploads"
uploads = CloudBucket(
    name="uploads",
    mount_path=mount_path,
    config=CloudBucketConfig(
        access_key="BEAM_S3_KEY",
        secret_key="BEAM_S3_SECRET",
    ),
)

@endpoint(name="image_endpoint", image=BeamImage().add_python_packages(["pillow"]), volumes=[uploads])
def image_endpoint(image_name: str):
    image_path = os.path.join(uploads.mount_path, image_name)
    image = Image.open(image_path)
    # do something with the image
    return {"message": "Image processed successfully"}
```

In order to correctly mount the S3 bucket, we need to make sure that our secrets are set. We can do this using the Beam CLI.

```bash theme={null}
beam secret create BEAM_S3_KEY "your-access-key"
beam secret create BEAM_S3_SECRET "your-secret-key"
```

Once again, we can deploy our endpoint with the command `beam deploy app.py:image_endpoint`.

To test this method, we can upload an image to the S3 bucket using the [AWS CLI](https://docs.aws.amazon.com/cli/latest/reference/s3/cp.html) and then pass the filename to our endpoint.

```bash theme={null}
aws s3 cp ./test.png s3://uploads/
```

The image will be uploaded to the S3 bucket and the endpoint will be able to read it. We can verify this by invoking our endpoint with the filename.

```bash theme={null}
curl -X POST "https://image-endpoint-53b4230-v1.app.beam.cloud" \
-H 'Connection: keep-alive' \
-H 'Content-Type: application/json' \
-H 'Authorization: Bearer <your-token>' \
-d '{"image_name": "test.png"}'
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Running Async Tasks

### What Are Task Queues?

Task Queues are great for deploying resource-intensive functions on Beam.

Instead of processing tasks immediately, the task queue enables you to add tasks to a queue and process them later, either sequentially or concurrently.

### Creating a Task Queue

You can run any function as a task queue by using the `task_queue` decorator:

```python theme={null}
from beam import task_queue, Output


@task_queue(cpu=1.0, memory=128)
def handler():
    result = 839 * 18

    # Save the result to a text file
    file_name = "result.txt"
    with open(file_name, "w") as f:
        f.write(f"The result is: {result}")

    # Upload task result to Beam to retrieve later
    Output(path=file_name).save()
```

You’ll be able to access the `result.txt` file when the task completes.

<Tip>
  **Endpoints vs. Task Queues**

Endpoints are RESTful APIs, designed for synchronous tasks that can complete in 180 seconds or less. For longer running tasks, you'll want to use an async [`task_queue`](/v2/task-queue/running-tasks) instead.
</Tip>

### Sending Async Requests

Because task queues run asynchronously, the API will return a Task ID.

**Example Request**

```bash Request theme={null}
  curl -X POST "https://9655d778-58c2-4c5d-8c55-03735b63607e.app.beam.cloud" \
   -H 'Authorization: Basic [YOUR_AUTH_TOKEN]' \
   -H 'Content-Type: application/json' \
   -d '{}'
```

**Example Response**

```bash Response theme={null}
{ "task_id": "edbcf7ff-e8ce-4199-8661-8e15ed880481" }
```

### Viewing Task Responses

Because `task_queue` is async, you will need to make a separate API call to retrieve the task output.

### Saving and Returning Output Files

You can save files using Beam's [Output](/v2/reference/sdk#output) class.

The code below saves a file, wraps it in an `Output`, and generates a URL that can be retrieved later:

```python app.py theme={null}
from beam import task_queue, Output


@task_queue(
    cpu=1.0,
    memory=128,
    gpu="A10G",
    callback_url="https://webhook.site/9b74f73d-9ec1-4c8e-adcc-07c78aafab6d",
)
def handler():
    sum = 839 * 18

    # Create a new text file with the result
    file_name = "sum.txt"

    # Write to new text file
    with open(file_name, "w") as f:
        f.write(f"The sum is: {sum}")

    # Save output
    output_file = Output(path=file_name)
    # Uploads the file to Beam storage
    output_file.save()
```

### Retrieving Results

There are two ways to retrieve response payloads:

1. Beam makes a webhook request to your server, based on the [`callback_url`](/v2/topics/callbacks) in your endpoint
2. Saving an `Output` and calling the `/task` API

#### Webhooks

If you've added a [`callback_url`](/v2/topics/callbacks) to your decorator, Beam will fire a webhook to your server with the task response when it completes:

```json theme={null}
{
  "data": {
    "url": "https://app.beam.cloud/output/id/00894876-38df-42c8-a098-879db17e1bf8"
  }
}
```

<Tip>
  For testing purposes, you can setup a temporary webhook URL using
  [https://webhook.site](https://webhook.site)
</Tip>

#### Polling for Results

`Output` payloads can be retrieved by polling the `task` API:

```bash theme={null}
curl -X GET \
  'https://api.beam.cloud/v2/task/{TASK_ID}/' \
  -H 'Authorization: Bearer [YOUR_AUTH_TOKEN]' \
  -H 'Content-Type: application/json'
```

Your Output will be available in the `outputs` list in the response:

```json theme={null}
{
  "id": "828a5f6b-0852-44cb-97dc-3c2105b745d3",
  "started_at": "2025-05-22T23:19:58.995396Z",
  "ended_at": "2025-05-22T23:19:59.061813Z",
  "status": "COMPLETE",
  "container_id": "taskqueue-2365b036-39df-408f-946f-b25025d1251a-bf09bf62",
  "updated_at": "2025-05-22T23:19:59.063168Z",
  "created_at": "2025-05-22T23:19:58.950594Z",
  "outputs": [
    {
      "name": "sum.txt",
      "url": "https://app.beam.cloud/output/id/c339b459-34de-4f0c-adb9-8be7c20951ce",
      "expires_in": 3600
    }
  ],
  "stats": {
    "active_containers": 1,
    "queue_depth": 0
  }
}
```

### Retry Behavior

Task Queues include a built-in retry system. If a task fails for any reason,
such as out-of-memory error or an application exception, your task will be
retried three times before automatically moving to a failed state.

### Programmatically Enqueuing Tasks

You can interact with the task queue either through an API (when deployed), or directly in Python through the `.put()` method.

<Tip>
  This is useful for queueing tasks programmatically without exposing an
  endpoint.
</Tip>

```python app.py theme={null}
from beam import task_queue, Image


@task_queue(
    cpu=1.0,
    memory=128,
    gpu="T4",
    image=Image(python_packages=["torch"]),
    keep_warm_seconds=1000,
)
def multiply(x):
    result = x * 2
    return {"result": result}

# Manually insert task into the queue
multiply.put(x=10)
```

If invoked directly from your local computer, the code above will produce this output:

```
$ python app.py

=> Building image
=> Using cached image
=> Syncing files
=> Files synced

Enqueued task: f0d205da-e74b-47ba-b7c3-8e1b9a3c0669
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Querying Task Status

You can check the status of any task by querying the `task` API:

```sh theme={null}
https://api.beam.cloud/v2/task/{TASK_ID}/
```

## Task Statuses

Your payload will return the status of the task. These are the possible statuses for a task:

| Status      | Description                                                                                                                                                                               |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PENDING`   | The task is enqueued and has not started yet.                                                                                                                                             |
| `RUNNING`   | The task is running.                                                                                                                                                                      |
| `COMPLETE`  | The task completed without any errors.                                                                                                                                                    |
| `RETRY`     | The task is being retried. Defaults to 3, unless `max_retries` is provided in the function decorator.                                                                                     |
| `CANCELLED` | The task was cancelled by the client.                                                                                                                                                     |
| `TIMEOUT`   | The task timed out, based on the `timeout` provided in the function decorator.                                                                                                            |
| `EXPIRED`   | The task remained in the queue and was never picked up by a worker. **For endpoints, this usually occurs when the task does not start running before the request timeout (180 seconds).** |
| `FAILED`    | The task did not complete successfully.                                                                                                                                                   |

### Request

```sh theme={null}
curl -X GET \
  'https://api.beam.cloud/v2/task/{TASK_ID}/' \
  -H 'Authorization: Bearer [YOUR_AUTH_TOKEN]' \
  -H 'Content-Type: application/json'
```

### Response

The response to `/task` returns the following data:

| Field                     | Type    | Description                                                                                                 |
| ------------------------- | ------- | ----------------------------------------------------------------------------------------------------------- |
| `id`                      | string  | The unique identifier of the task.                                                                          |
| `started_at`              | string  | The timestamp when the task started, in ISO 8601 format. Null if the task hasn't started yet.               |
| `ended_at`                | string  | The timestamp when the task ended, in ISO 8601 format. Null if the task is still running or hasn't started. |
| `status`                  | string  | The current status of the task (e.g., COMPLETE, RUNNING, etc.).                                             |
| `container_id`            | string  | The identifier of the container running the task.                                                           |
| `updated_at`              | string  | The timestamp when the task was last updated, in ISO 8601 format.                                           |
| `created_at`              | string  | The timestamp when the task was created, in ISO 8601 format.                                                |
| `outputs`                 | array   | An array containing the outputs of the task.                                                                |
| `stats`                   | object  | An object containing statistics about the task's execution environment.                                     |
| `stats.active_containers` | integer | The number of active containers for the task.                                                               |
| `stats.queue_depth`       | integer | The depth of the queue for the deployment.                                                                  |
| `stub`                    | object  | An object containing detailed information about the task's configuration and deployment.                    |
| `stub.id`                 | string  | The identifier of the deployment stub.                                                                      |
| `stub.name`               | string  | The name of the deployment stub.                                                                            |
| `stub.type`               | string  | The type of the deployment stub.                                                                            |
| `stub.config`             | string  | The configuration details of the deployment stub in JSON format.                                            |
| `stub.config_version`     | integer | The version number of the deployment stub configuration.                                                    |
| `stub.object_id`          | integer | The object identifier associated with the deployment stub.                                                  |
| `stub.created_at`         | string  | The timestamp when the deployment stub was created, in ISO 8601 format.                                     |
| `stub.updated_at`         | string  | The timestamp when the deployment stub was last updated, in ISO 8601 format.                                |

Here's what the response payload looks like as JSON:

```json theme={null}
{
  "id": "c5f01c46-4eb3-4021-9d5f-eae9a08c4aad",
  "started_at": "2025-05-22T22:49:03.839612Z",
  "ended_at": "2025-05-22T22:49:03.913964Z",
  "status": "COMPLETE",
  "container_id": "taskqueue-da2e6878-e202-40d4-9b7a-21706f3a2b13-c23f1166",
  "updated_at": "2025-05-22T22:49:03.915891Z",
  "created_at": "2025-05-22T22:49:03.832363Z",
  "outputs": [],
  "stats": {
    "active_containers": 1,
    "queue_depth": 0
  },
  "stub": {
    "id": "da2e6878-e202-40d4-9b7a-21706f3a2b13",
    "name": "taskqueue/serve/app:handler",
    "type": "taskqueue/serve",
    "config": {
      "runtime": {
        "cpu": 1000,
        "gpu": "",
        "gpu_count": 1,
        "memory": 128,
        "image_id": "d055bc4ee4ad0e61",
        "gpus": ["A10G"]
      },
      "handler": "app:handler",
      "on_start": "",
      "on_deploy": "",
      "on_deploy_stub_id": "",
      "python_version": "python3",
      "keep_warm_seconds": 10,
      "max_pending_tasks": 100,
      "callback_url": "",
      "task_policy": {
        "max_retries": 3,
        "timeout": 3600,
        "expires": "0001-01-01T00:00:00Z",
        "ttl": 7200
      },
      "workers": 1,
      "concurrent_requests": 1,
      "authorized": true,
      "volumes": null,
      "autoscaler": {
        "type": "queue_depth",
        "max_containers": 1,
        "tasks_per_container": 1,
        "min_containers": 0
      },
      "extra": {},
      "checkpoint_enabled": false,
      "work_dir": "",
      "entry_point": null,
      "ports": null
    },
    "config_version": 0,
    "created_at": "2025-05-22T22:48:57.156033Z",
    "updated_at": "2025-05-22T22:48:57.156033Z"
  },
  "deployment": {
    "name": null,
    "version": null
  }
}
```

## Cancelling Tasks

Tasks can be cancelled through the `api.beam.cloud/v2/task/cancel/` endpoint.

### Request

```bash theme={null}
curl -X DELETE --compressed 'https://api.beam.cloud/v2/task/cancel/' \
  -H 'Authorization: Bearer [YOUR_TOKEN]' \
  -H 'Content-Type: application/json' \
  -d '{"task_ids": ["TASK_ID"]}'
```

This API accepts a list of tasks, which can be passed in like this:

```json theme={null}
{
  "task_ids": [
    "70101e46-269c-496b-bc8b-1f7ceeee2cce",
    "81bdd7a3-3622-4ee0-8024-733227d511cd",
    "7679fb12-94bb-4619-9bc5-3bd9c4811dca"
  ]
}
```

### Response

`200`

```json theme={null}
{}
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Running Functions Remotely

> A short guide on using Beam to run one-off functions in the cloud

You can add a decorator to any Python function to run it remotely on Beam:

```python app.py theme={null}
from beam import function


@function()
def handler():
    return {"hello world"}

if __name__ == "__main__":
    handler.remote()
```

Just run this like a normal Python file, and the code will run on Beam's cloud and stream the response back to your shell.

```sh theme={null}
$ python app.py

=> Building image
=> Using cached image
=> Syncing files
=> Uploading
=> Files synced
=> Running function: <app:handler>
Loading image <d055bc4ee4ad0e61>...
Loaded image <d055bc4ee4ad0e61>, took: 3.131485ms
=> Function complete <b9ba6b86-6dfa-4bf3-89d0-75262bcc06f0>
```

<Info>
  By default, Beam will sync all the files in your working directory to the
  remote container. This allows you to use the files you have locally while
  developing. If you want to prevent some files from getting uploaded, you can
  create a [`.beamignore`](/v2/reference/cli#ignore-local-files).
</Info>

## Passing Function Args

You can also pass arguments to your function just like normal Python functions:

```python app.py theme={null}
from beam import function

@function()
def greet(name: str):
    return f"Hello {name}"

if __name__ == "__main__":
    print(greet.remote("World"))  # "Hello World"
```

## Task Timeouts

You can set timeouts on tasks. Timeouts are set in seconds:

```python theme={null}
from beam import function


# Set a 24 hour timeout
@function(timeout=86400)
def long_timeout():
    return {"hello world"}


# Disable timeouts completely
@function(timeout=-1)
def no_timeout():
    return {"message": "hello world"}
```

## Running Tasks in the Background

By default, remote functions will stop when you close your local Python process or exit your shell.

You can override this behavior and keep the function running in the background by setting `headless=False` in
your function decorator.

```python theme={null}
import time
from beam import function


# Run the function in the background
@function(headless=True)
def handler():
    for i in range(100):
        print(i)
        time.sleep(1)

    return {"message": "This is running in the background"}


if __name__ == "__main__":
    handler.remote()
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Scheduled Jobs

> How to run workloads on a schedule.

## Run Scheduled Jobs

Use the `@schedule` decorator to define a scheduled job.

```python theme={null}
from beam import schedule


@schedule(when="@weekly", name="weekly-task")
def task():
    print("Hi, from your weekly scheduled task!")
```

To schedule it, run `beam deploy`:

```sh theme={null}
beam deploy app.py:task
```

You'll see the upcoming jobs listed in the console.

```sh theme={null}
=> Deployed 🎉
=> Schedule details
Schedule: @hourly
Upcoming:
  1. 2024-08-30 18:00:00 UTC (2024-08-30 14:00:00 EDT)
  2. 2024-08-30 19:00:00 UTC (2024-08-30 15:00:00 EDT)
  3. 2024-08-30 20:00:00 UTC (2024-08-30 16:00:00 EDT)
```

## Scheduling Options

The following predefined schedules can be used in the `when` parameter:

| **Predefined Schedule**    | **Description**                                            | **Cron Expression** |
| -------------------------- | ---------------------------------------------------------- | ------------------- |
| `@yearly` (or `@annually`) | Run once a year at midnight on January 1st                 | `0 0 1 1 *`         |
| `@monthly`                 | Run once a month at midnight on the first day of the month | `0 0 1 * *`         |
| `@weekly`                  | Run once a week at midnight on Sunday                      | `0 0 * * 0`         |
| `@daily` (or `@midnight`)  | Run once a day at midnight                                 | `0 0 * * *`         |
| `@hourly`                  | Run once an hour at the beginning of the hour              | `0 * * * *`         |

## Stopping Scheduled Jobs

You can stop a scheduled job from running by using the `beam deployment stop` CLI command.

First, list the upcoming jobs with `beam deployment list`:

```sh theme={null}
  ID                       Name                   Active   Version   Created At      Updated At      Stub Name                 Workspace Name
 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  10c192b6-6489-42c9-a3…   schedule               Yes            2   9 minutes ago   9 minutes ago   schedule/deployment/ap…   f6fa28
```

Then reference the **Deployment ID** to stop a job:

```sh theme={null}
$ beam deployment stop 10c192b6-6489-42c9-a3

Stopped 10c192b6-6489-42c9-a3bf-75c52ad1816b
```

## Gotchas

<Tip>
  If you deploy a new version of your scheduled job, the previous schedule will be disabled.
</Tip>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Queues

> Using Beam's distributed Queue to coordinate between tasks

Beam includes a concurrency-safe distributed queue, accessible both locally and within remote containers.

Serialization is done using cloudpickle, so any object that supported by that should work here. The interface is that of a standard python queue.

Because this is backed by a distributed queue, it will persist between runs.

In the example below, we run one function remotely on Beam and another locally. The remote function puts a value in the queue, and the local function pops it out and prints it. The output will be `beam me up`.

```python Simple Queue theme={null}
from beam import Queue, function


@function()
def first():
    q = Queue(name="q")
    q.put("beam me up")
    return


@function()
def second():
    q = Queue(name="q")
    print(q.pop())
    return


if __name__ == '__main__':
    first.remote()
    second.local()
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Host a Web Service

[`Pod`](/v2/reference/sdk#pod) provides a way to run serverless containers on the cloud. It enables you to quickly launch a container as an HTTPS server that you can access from a web browser.

Pods run in isolated containers, allowing you to run untrusted code safely from your host system.

This can be used for a variety of use cases, such as:

- Hosting GUIs, like Jupyter Notebooks, Streamlit or Reflex apps, and ComfyUI
- Testing code in an isolated environment as part of a CI/CD pipeline
- Securely executing code generated by LLMs

...and much more (if you've got a cool use case, [let us know!](https://join.slack.com/t/beam-cloud/shared_invite/zt-3enuvj3r7-OeAzVPYvyqQHy9avNrLL0w))

# Launching Cloud Containers

Containers can be launched programmatically through the Python SDK, or with the Beam CLI.

For example, the following code is used to launch a cloud-hosted Jupyter Notebook:

<CodeGroup>
  ```python Python theme={null}
  from beam import Image, Pod

notebook = Pod(
image=Image(base_image="jupyter/base-notebook:latest"),
ports=[8888],
cpu=1,
memory=1024,
env={
"NOTEBOOK_ARGS": "--ip='' --NotebookApp.token='' --NotebookApp.notebook_dir=/tmp"
},
entrypoint=["start-notebook.py"],
)

nb = notebook.create()

print("✨ Container hosted at:", nb.url)

````

```shell CLI theme={null}
beam run --image jupyter/base-notebook:latest --ports 8888 \
  --env NOTEBOOK_ARGS="--ip='' --NotebookApp.token='' --NotebookApp.notebook_dir=/tmp" \
  --entrypoint "start-notebook.py"
````

</CodeGroup>

When this code is executed, Beam will launch a container and expose it as a publicly available HTTPS server:

```
$ python app.py

=> Building image
=> Using cached image
=> Syncing files
=> Creating container
=> Container created successfully ===> pod-2929b184-b445-4f23-abc6-7c4b151001da-ec86d9ac

✨ Container hosted at: https://2929b184-b445-4f23-abc6-7c4b151001da-8888.app.beam.cloud
```

### Accessing Containers via HTTP

You can then enter this URL in the browser to interact with your hosted container instance:

<Frame>
  <img src="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=50edc088e6b8d4e4caae0b0a1f343370" data-og-width="1910" width="1910" data-og-height="906" height="906" data-path="img/v2/nb.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?w=280&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=690b9755cf1497a551b814f6fe483ba4 280w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?w=560&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=5e6cc463b185754ff178eef3e57fe504 560w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?w=840&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=7445f58b34e476e37c964544d7c7991c 840w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?w=1100&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=7e6b51a8d53c942d63ae2db30763ff38 1100w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?w=1650&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=f0589d6ccb000cb16771dd89277b818b 1650w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/nb.png?w=2500&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=fe6781f78f80ec00ab1645609916ad5d 2500w" />
</Frame>

### Securely Executing Untrusted Code

Beam's containers are launched in isolated environments from your host system, making it safe to execute untrusted or LLM-generated code.

## Parameters

Pods can be heavily customized to fit your needs.

### Using Custom Images

You can customize the container image using the [`Image`](/v2/reference/sdk#image) object. This can be customized with Python packages, shell commands, Conda packages, and much more.

<CodeGroup>
  ```python Python theme={null}
  from beam import Image, Pod

pod = Pod(
image=Image(base_image="jupyter/base-notebook:latest"),
entrypoint=["start-notebook.py"],
)

````

```shell CLI theme={null}
beam run --image jupyter/base-notebook:latest --entrypoint "start-notebook.py"
````

</CodeGroup>

### Specifying Entry Points

An _entry point_ is the command or script that will run when the container starts. You can interact with Pods using the CLI or the Python SDK.

<CodeGroup>
  ```python Python theme={null}
  from beam import Image, Pod

pod = Pod(
image=Image(base_image="jupyter/base-notebook:latest"),
entrypoint=["start-notebook.py"],
)

pod.create()

````

```shell CLI theme={null}
beam run \
  --image jupyter/base-notebook:latest \
  --entrypoint "start-notebook.py"
````

</CodeGroup>

### Passing Environment Variables

You can pass environment variables into your container for credentials or other parameters. Like entry points, environment variables can be defined in both the CLI or the Python SDK:

<CodeGroup>
  ```python Python theme={null}
  from beam import Image, Pod

Pod(
image=Image(base_image="jupyter/base-notebook:latest"),
env={"NOTEBOOK_ARGS": "--ip='' --NotebookApp.token='' --NotebookApp.notebook_dir=/tmp"},
entrypoint=["start-notebook.py"],
)

````

```shell CLI theme={null}
beam run \
  --image jupyter/base-notebook:latest \
  --env NOTEBOOK_ARGS="--ip='' --NotebookApp.token='' --NotebookApp.notebook_dir=/tmp" \
  --entrypoint "start-notebook.py"
````

</CodeGroup>

## Deploying a Pod

Pods can be deployed as persistent endpoints using the `beam deploy` command.

<Warning>
  When deploying a Pod, don't forget to include the `name` field.
</Warning>

```python app.py theme={null}
from beam import Pod

pod = Pod(
    name="my-deployed-pod",
    cpu=2,
    memory="1Gi",
    ports=[8000],
    entrypoint=["python", "-m", "http.server", "8000"],
)
```

```sh theme={null}
beam deploy app.py:pod
```

## Terminating a Pod

Pod instances can be terminated directly using the `terminate()` method.

Alternatively, you can terminate the container the Pod is running on by using the `beam container stop <container-id>` command.

```python theme={null}
from beam import Pod

# Initialize a pod
notebook = Pod()

# Launch the pod
notebook.create()

# Terminate the pod
notebook.terminate()
```

## Lifecycle

### Timeouts

Pods are serverless and automatically scale-to-zero.

By default, pods will be terminated after 10 minutes without any active connections to the hosted URL or until the process exits by itself. Making a connection request (i.e. accessing the URL in your browser) will keep the container alive until the timeout is reached.

You can set a custom timeout by passing the `keep-warm-seconds` parameter when creating a pod. By specifying -1, the pod will not spin down to due inactivity, and will remain up until either the entrypoint process exits, or you explicitly stop the container.

**Keep Alive for 5 minutes**

```python theme={null}
beam run --image jupyter/base-notebook:latest --keep-warm-seconds 300
```

**Keep Alive Indefinitely**

<Tip>_There is no upper limit on the duration of a session_.</Tip>

```python theme={null}
beam run --image jupyter/base-notebook:latest --keep-warm-seconds -1
```

### List Running Pods

You can list all running Pods using the `beam container list` command.

```bash theme={null}
$ beam container list

  ID                                                  Status    Stub ID                                Deployment ID   Scheduled At    Uptime
 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  pod-58a38dba-8b7b-4db3-b002-436c6d9b4858-a613eecd   RUNNING   58a38dba-8b7b-4db3-b002-436c6d9b4858                   5 seconds ago   4 seconds

  1 items
```

You can kill any container by running `beam container stop <container-id>`.

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Networking

## Exposing Ports

You can expose TCP ports to the outside world by specifying the ports you want to expose in the `ports` parameter.

`ports` accepts a list, so you can expose multiple ports too.

In the example below, we expose two ports:

- `8888` for a Jupyter Notebook server
- `3000` for a separate application or web server

```python theme={null}
from beam import Image, Pod

pod = Pod(
    image=Image(base_image="jupyter/base-notebook:latest"),
    ports=[8888, 3000],
    entrypoint=["start-notebook.py"],
)
```

Once your Pod is running, both ports will be available at a public URL.

## Network Security

### Blocking Outbound Traffic

You can block all outbound network access from your Pod while still allowing inbound connections to exposed ports. This is useful for security-sensitive workloads that shouldn't communicate with external services.

```python theme={null}
from beam import Image, Pod

pod = Pod(
    image=Image(base_image="python:3.11-slim"),
    ports=[8000],
    block_network=True,  # Block all outbound traffic
    entrypoint=["python", "-m", "http.server", "8000"],
)
```

With `block_network=True`, the Pod can receive requests on exposed ports but cannot make outbound connections to external services.

### Allow Lists (CIDR Ranges)

For more fine-grained control, you can specify an allow list of CIDR ranges that your Pod is permitted to connect to. All other outbound traffic will be blocked.

```python theme={null}
from beam import Image, Pod

pod = Pod(
    image=Image(base_image="python:3.11-slim"),
    ports=[8000],
    allow_list=[
        "8.8.8.8/32",      # Allow Google DNS
        "10.0.0.0/8",      # Allow private network range
        "2001:db8::/32",   # Allow IPv6 range
    ],
    entrypoint=["python", "app.py"],
)
```

**Important Notes:**

- Maximum of 10 CIDR entries per Pod
- Supports both IPv4 and IPv6 addresses
- Must use proper CIDR notation (e.g., `"8.8.8.8/32"` for a single IP)
- Cannot use `allow_list` and `block_network` together - they are mutually exclusive
- Invalid CIDR values will trigger an error at creation time

## Static IPs

Pods are served in a static IP range, making it possible to whitelist the Beam IP range from the client.

For the static IP range, send us a message in [Slack](https://join.slack.com/t/beam-cloud/shared_invite/zt-3enuvj3r7-OeAzVPYvyqQHy9avNrLL0w).

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Integrate into CI/CD

> You can integrate Beam into an existing CI/CD process to deploy your code automatically.

## Automated Deploys

It's fairly straightforward to setup automation for deploying your code to Beam. At a high level, the following steps are all you need:

```sh theme={null}
pip3 install --upgrade pip
pip3 install beam-client
beam configure default --token $BEAM_TOKEN
beam deploy file.py:function
```

## Example: Github Actions

You can setup a Github workflow to deploy your code whenever a new commit is made to your Git repo.

### Setup Environment Variables

First, add your `BEAM_TOKEN` to your [Github Secrets](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions#creating-secrets-for-a-repository):

<Frame>
  <img src="https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=2906e3b3728724d4bdc029d0e96401a3" data-og-width="1296" width="1296" data-og-height="142" height="142" data-path="img/deployment/github-settings.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?w=280&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=c865423cf51d8d3bd9cf8b89ca0508c9 280w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?w=560&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=6ded46014fdb08bebd8619a30d4bef0a 560w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?w=840&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=58aa6408530971b9793eb3d355f18399 840w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?w=1100&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=fff59d23be558636badf1a30e812babe 1100w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?w=1650&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=57083c1b2f4e312274f5018e49037e1f 1650w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-settings.png?w=2500&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=e4eb6e8c896c861e5b76c2754dc326a6 2500w" />
</Frame>

<Frame>
  <img src="https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=46c8b8d63080aa96d6e22c4abf2fe732" data-og-width="1294" width="1294" data-og-height="370" height="370" data-path="img/deployment/github-create.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?w=280&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=95d61ed076cc73cca85f3c1dc098d9a0 280w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?w=560&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=c964c8e84fcf7c640168d2fae2e7b051 560w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?w=840&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=811f935a7eefcbc8a511f5816382dde2 840w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?w=1100&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=8f54994a59e864e62b1a36a436352440 1100w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?w=1650&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=ec6969f2d8ae7b30e7b9525ad24d6807 1650w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/github-create.png?w=2500&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=44c0edef695f2ec902dfca3193d40b43 2500w" />
</Frame>

### Create Actions file

<Info>
  For a detailed walk-through of this step, [Github's
  documentation](https://docs.github.com/en/actions/quickstart) is the best
  resource.
</Info>

1. Create a directory called `.github/workflows` in your project.
2. In the `.github/workflows` directory, create a file named `beam-actions.yml`

### Deploying to Different Environments

You might want to setup separate Beam apps for your `staging` or `prod` environments.

In your Beam app, you can setup your app name to dynamically update based on the Github branch you've deployed to. `BEAM_DEPLOY_ENV` will get set in our Github Actions script, based on the branch name:

```python app.py theme={null}
from beam import endpoint
import os

@endpoint(name=f'app-{os.getenv("BEAM_DEPLOY_ENV", "staging")}')
def handler():
  return {}
```

If you push to the `main` branch, the app `app-prod` will be deployed. If you push to the `staging` branch, `app-staging` will be deployed. You can customize this with your own branch names.

Here's what the Github Action looks like. Make sure you've added a `BEAM_TOKEN` to your [Github Secrets](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions#creating-secrets-for-a-repository):

```yaml beam-actions.yml theme={null}
name: Deploy to Beam

on:
  push:
    branches:
      - main
      - staging

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set environment variables
        run: |
          if [[ "${{ github.ref }}" == 'refs/heads/main' ]]; then
            echo "Setting environment variables: PROD"
            echo "BEAM_DEPLOY_ENV=prod" >> $GITHUB_ENV
          elif [[ "${{ github.ref }}" == 'refs/heads/staging' ]]; then
            echo "Setting environment variables: STAGING"
            echo "BEAM_DEPLOY_ENV=staging" >> $GITHUB_ENV
          fi

      - name: Authenticate and deploy to Beam
        env:
          BEAM_TOKEN: ${{ secrets.BEAM_TOKEN }}
        run: |
          pip3 install --upgrade pip
          pip3 install beam-client
          pip3 install fastapi

          echo "beam configure default --token $BEAM_TOKEN"
          beam configure default --token $BEAM_TOKEN
          beam deploy app.py:function
```

When you push to either `main` or `staging`, a new app will be deployed for each push:

<Frame>
  <img src="https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=512d52c25d7bbd4806755830f225508d" data-og-width="651" width="651" data-og-height="199" height="199" data-path="img/deployment/ci-env.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?w=280&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=cb56d97158763fbf6d00cc05c435a7c4 280w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?w=560&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=908a912830cabec955f8e25539bd5083 560w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?w=840&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=bb3d4906accc34ed6cd6dda09d5fa5cc 840w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?w=1100&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=8784cff7fed1479eea10278363174c43 1100w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?w=1650&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=f692dab4efdd151fd73ef268b5b5ecb9 1650w, https://mintcdn.com/slai-beam/cYxAFgZcnH6nQdWb/img/deployment/ci-env.png?w=2500&fit=max&auto=format&n=cYxAFgZcnH6nQdWb&q=85&s=a7a3574f33f394391e1dbdb2fa1dda97 2500w" />
</Frame>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Timeouts and Retries

You can customize the default timeout and retry behavior for your tasks.

# Timeouts

### Default Timeouts

Tasks automatically timeout after 20 minutes _if they haven't started running_. This default exists to prevent stuck tasks from consuming compute resources and potentially blocking other tasks in the queue.

### Customizing Timeouts

You can specify your own timeouts. Timeouts can be used for endpoints, task queues, and functions:

```python timeout.py theme={null}
from beam import function
import time


@function(timeout=600) # Override default timeout
def timeout():
    import time

    # Without the timeout specified above, this function would timeout at 300s
    time.sleep(350)


if __name__ == "__main__":
    timeout()
```

# Retries

Beam includes retry logic, which can be customized using the parameters below.

### Max Retries

You can configure tasks to automatically retry based on a specific exception in your app.

In the example below, we'll specify `retries` and `retry_for`:

```python timeout.py theme={null}
from beam import task_queue


@task_queue(retries=2, retry_for=[Exception])  # Override default retry logic
def handler():
    raise Exception("Something went wrong, retry please!")
```

### Retry for Exceptions

When the task is invoked, we'll see the exception get caught and the task automatically retry:

```sh theme={null}
Running task <87067d0e-5900-413b-a3a3-5ee4c85706ad>
Traceback (most recent call last):
  File "/mnt/code/app.py", line 6, in handler
    raise Exception("Something went wrong, retry please!")

Exception: Something went wrong, retry please!
retry_for error caught: Exception('Something went wrong, retry please!')
Retrying task <87067d0e-5900-413b-a3a3-5ee4c85706ad> after Exception exception

Running task <87067d0e-5900-413b-a3a3-5ee4c85706ad>
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Public Endpoints

> Deploying public web endpoints on Beam

## Creating a Public Endpoint

By default, endpoints are private and require a bearer token to access. You can remove the authentication requirement for endpoints using the `Authorized=False` argument:

```python auth.py theme={null}
from beam import endpoint


@endpoint(authorized=False)  # Disable authentication
def create_public_endpoint():

    print("This API can be invoked without an auth token")
    return {"success": "true"}
```

## Invoking a Public Endpoint

Public endpoints have slightly different URL schemes than private ones:

```
https://app.beam.cloud/endpoint/public/[STUB-ID]
```

```
https://app.beam.cloud/endpoint/public/4f78aaae-f35c-4eb0-9236-cdd34509bad8
```

<Tip>
  You can find your **Stub ID** on the deployment detail page in the web dashboard.
</Tip>

You can view your the API URL by clicking the `Call API` button on the deployment detail page in the web dashboard.

A full request to a public endpoint might look something like this:

```bash theme={null}
curl -X POST \
--compressed 'https://app.beam.cloud/endpoint/public/4f78aaae-f35c-4eb0-9236-cdd34509bad8' \
-H 'Connection: keep-alive' \
-H 'Content-Type: application/json' \
-d '{}'
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Task Callbacks

> Setup a callback to your server when a task finishes running

If you supply a `callback_url` argument to your function decorator, Beam will make a POST request to your server whenever a task finishes running. _Callbacks fire for both successful and failed tasks._

Callbacks include the Beam Task ID in the request headers, and the task response URL-encoded in the request body.

<Tip>
  For testing purposes, you can setup a temporary webhook URL using
  [https://webhook.site](https://webhook.site)
</Tip>

## Registering a Callback URL

Callbacks can be added onto endpoints, functions, and task queues:

```python theme={null}
from beam import function


@function(callback_url="https://your-server.io")
def handler(x):
    return {"result": x}

if __name__ == "__main__":
    handler.remote(x=10)
```

## Callback format

### Data Payload

The callback will send the response from your function as JSON, in the `data` field:

```
{
  "data": {
    "result": 10
  }
}
```

## Request headers

The request headers include the following fields:

- `x-task-timestamp` -- timestamp the task was created.
- `x-task-signature` -- signature to verify that the request was sent from Beam.
- `x-task-status` -- status of the task.
- `x-task-id` -- the task ID.

## Request Level Callbacks

There are cases where you might want to define a different `callback_url` for each request, for example if you have different environments for staging and prod.

You can pass `callback_url` as a payload to anything you're running on Beam, and we'll use that as the callback for the request:

```sh theme={null}
curl -X POST \
  --compressed 'https://multiply-712408b-v1.app.beam.cloud' \
  -H 'Authorization: [YOUR_AUTH_TOKEN]' \
  -H 'Content-Type: application/json' \
  -d '{"callback_url": "https://webhook.site/341d3777-cdd0-4c7e-82cb-dcc06ea4f774"}'
```

<Warning>
  When using request-level callbacks, you must include either the `callback_url` value or kwargs (`**inputs`) as input to the handler function:

```python theme={null}
from beam import endpoint


@endpoint()
def handler(callback_url): # Make sure to pass this value!
    return {"response": "true"}

@endpoint()
def handler(**inputs): # You can use kwargs too
    return {"response": "true"}
```

</Warning>

## Verifying Requests

### Timestamp Verification

To secure your server against replay attacks, a **timestamp** and **signature** are included in the callback request headers.

As a best-practice, it is wise to check the timestamp header of each callback request. If the timestamp is over 5s old, there is a risk that the callback was not fired from Beam.

### Signature Verification

The most secure way of verifying a callback request is through **signature verification**.

Your Signature Token can be found in the dashboard, on the `Settings` -> `General` page.

<Frame>
  <img src="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=287d90d36ce266911078ef9acb7a8ad7" data-og-width="2474" width="2474" data-og-height="1006" height="1006" data-path="img/v2/callback-signature.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?w=280&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=83f6ac0ba6638897cfd44ee2714bf279 280w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?w=560&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=1d19b106eb6d3577dc6d69610802e32d 560w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?w=840&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=72169d6c2935fb2e0ef9b659198e5f73 840w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?w=1100&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=71089f18ca11f08e9b0ca41b937d3d68 1100w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?w=1650&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=619164a14c3f3b6cf79863dbd2894957 1650w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/callback-signature.png?w=2500&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=4abb3b8cdd6184884eaec96c75bea972 2500w" />
</Frame>

#### Validating a Signature

The callback request will include a header field called `x-task-signature`.

`x-task-signature` is a unique signature generated by converting the request body to base64, concatenating it with the timestamp, and signing it with your Beam **signature token**.

The code below shows how to validate a callback signature:

```python theme={null}
import base64
import hashlib
import hmac


def verify_signature(
    request_body: bytes, secret_key: str, timestamp: int, signature: str
):
    # Encode request body to Base64
    base64_payload = base64.b64encode(request_body).decode()

    # Create data to sign by concatenating base64 payload with timestamp
    data_to_sign = f"{base64_payload}:{timestamp}"

    # Initialize HMAC with SHA256 and secret key
    h = hmac.new(secret_key.encode(), data_to_sign.encode(), hashlib.sha256)

    # Compute the HMAC signature
    computed_signature = h.hexdigest()
    assert signature == computed_signature
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Send Events Between Apps

There are certain cases where you'll want to send events between different apps running on Beam.

A common scenario is if you have a model inference and retraining pipeline, where the inference app (App #1) needs to use the latest version of a trained model (App #2).

<Card title="View the Code" icon="github" href="https://github.com/beam-cloud/examples/tree/main/experimental/signals">
  See the code for this example on Github.
</Card>

## Invoking Functions in Other Apps

This example demonstrates how to invoke functions in other apps on Beam. Specifically, we cover the scenario with an inference and a retraining function.

The retraining function needs a way to tell the inference function to use the latest model.

We use an `experimental.Signal()`, which is a special type of event listener that can be triggered from the retrain function.

### App 1: Retraining App

This is the retraining app. Below, we register a `Signal` that will fire an event to our inference app, which is subscribed to this Signal event.

```python theme={null}
from beam import endpoint, experimental

@endpoint(name="trainer")
def train():
    # Send a signal to another app letting it know that it needs to reload the models
    s = experimental.Signal(name="reload-model")
    s.set(ttl=60)
```

### App 2: Inference App

Below is the inference app, which needs to reload the `on_start` function when retraining is finished.

You'll notice that a Signal is registered with a handler that tells us which function to run when an event is fired.

```python theme={null}
from beam import endpoint, Volume, experimental, Image

VOLUME_NAME = "brand_classifier"
CACHE_PATH = f"./{VOLUME_NAME}-cache"


def load_latest_model():
    # Preload and return the model and tokenizer
    global model, tokenizer
    print("Loading latest...")
    model = lambda x: x + 1  # This is just example code

    s.clear()  # Clear the signal so it doesn't fire again


# Set a signal handler - when invoked, it will run the handler function
s = experimental.Signal(
    name="reload-model",
    handler=load_latest_model,
)


@endpoint(
    name="inference",
    volumes=[Volume(name=VOLUME_NAME, mount_path=CACHE_PATH)],
    image=Image(python_packages=["transformers", "torch"]),
    on_start=load_latest_model,
)
def predict(**inputs):
    global model, tokenizer  # These will have the latest values

    return {"success": "true"}
```

To test this example, you can open two terminal windows:

- In window 1, serve and invoke the inference function
- In window 2, serve and invoke the retrain function

Look at the logs in window 1 -- you'll notice that the signal has fired, and `load_latest_model` ran again.

## Clearing Signals

Signals will refresh every 1 second while a container is running, until `signal.clear()` is called. It is recommended to run `signal.clear()` after each signal invovocation.

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Runtime Variables

> Accessing information about the runtime while running tasks

## Available Runtime Variables

In order to access information about the runtime while running a task, you can use the `context` value.

`context` includes important contextual information about the runtime, like the current `task_id` and `callback_url`.

| Field Name       | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `container_id`   | Unique identifier for a container                      |
| `stub_id`        | Identifier for a stub                                  |
| `stub_type`      | Type of the stub (function, endpoint, task queue, etc) |
| `callback_url`   | URL called when the task status changes                |
| `task_id`        | Identifier for the specific task                       |
| `timeout`        | Maximum time allowed for the task to run (seconds)     |
| `on_start_value` | Any values returned from the `on_start` function       |
| `bind_port`      | Port number to bind a service to                       |
| `python_version` | Version of Python to be used                           |

## Using a Runtime Variable

Any of the fields above can be accessed on the `context` variable:

```python theme={null}
from beam import task_queue

@task_queue()
def handler(context):
    task_id = context.task_id
    return {}
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Remote vs. Local Environment

## Differences Between the Remote and Local Environments

Typically, your apps that run on Beam will be using packages that you don't have installed locally.

If your Beam app uses packages that aren't installed locally, you'll need to ensure your Python interpreter doesn't try to load these packages locally.

## Avoiding Import Errors

There are two ways to avoid import errors when using packages that aren't installed locally.

### Import Packages Inline

Importing packages inline is safe because the functions will only be invoked in the remote Beam environment that has these packages installed.

```python theme={null}
from beam import endpoint, Image


@endpoint(image=Image(python_packages=["torch", "pandas", "numpy"]))
def handler():
    import torch
    import pandas
    import numpy
```

### Use `env.is_remote()`

An alternative to using inline imports is to use a special check called `env.is_remote()` to conditionally import packages _only_ when inside the remote environment.

```python theme={null}
from beam import env


if env.is_remote():
    import torch
    import pandas
    import numpy
```

This command checks whether the Python script is running remotely on Beam, and will only try to import the packages in its scope if it is.

<Warning>
  While it might be tempting to use the `env.is_remote()` flag for other logic in your app, this command should only be used for package imports.
</Warning>

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Overview

> Beta9 is the open source project that powers Beam

## Beam vs. Beta9

**Beam and Beta9 have similar functionality.**

You can switch between either product by changing the SDK imports and CLI commands used:

|              | [beam.cloud](https://beam.cloud) | [Beta9](https://github.com/beam-cloud/beta9/) |
| ------------ | -------------------------------- | --------------------------------------------- |
| Installation | `pip install beam-client`        | `pip install beta9`                           |
| Imports      | `from beam import endpoint`      | `from beta9 import endpoint`                  |
| CLI Commands | `beam serve app.py:function`     | `beta9 serve app.py:function`                 |

## Self-Hosting Beta9

<CardGroup cols={2}>
  <Card title="Self-Host on AWS" icon="aws" href="/v2/self-hosting/aws" color="#ea5a0c" />

  <Card title="Self-Host Locally" icon="computer" href="/v2/self-hosting/local-machine" color="#0285c7" />
</CardGroup>

## Architecture

<Frame>
  <img src="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=f3ca1404db8aab63b72860b77b17aa1c" data-og-width="1497" width="1497" data-og-height="520" height="520" data-path="img/v2/beta9.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?w=280&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=042674e46952226b50df3a4c28d87a92 280w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?w=560&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=3a539d74d5b300dbd20b3fb429089f67 560w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?w=840&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=7f033bcce16d2d3562f2b1057fa249f8 840w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?w=1100&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=db7c6d395d2e561a837b55ea1b7b889f 1100w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?w=1650&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=8313c375cf033086f8fe9e7a6c710284 1650w, https://mintcdn.com/slai-beam/vg5aTEbpFmupCYom/img/v2/beta9.png?w=2500&fit=max&auto=format&n=vg5aTEbpFmupCYom&q=85&s=1c28f22383db791ee7029bf10bdb945c 2500w" />
</Frame>

## Contributor Guide

We welcome contributions, big or small! These are the most helpful things for us:

- Rank features in our roadmap
- Open a PR
- Submit a [feature request](https://github.com/beam-cloud/beta9/issues/new?assignees=&labels=&projects=&template=feature-request.md&title=) or [bug report](https://github.com/beam-cloud/beta9/issues/new?assignees=&labels=&projects=&template=bug-report.md&title=)

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt

# Local Machine

> Learn how to deploy Beam OSS (Beta9) to your local machine.

## Prerequisites

- Kubernetes
- Helm and kubectl
- Beta9 CLI

## Dependencies

Beta9 uses an S3-compatible object storage system for its file system. In this example, we'll deploy localstack.

<Note>
  Without a Localstack license, its data is temporary. If its pod is deleted, the data will be lost. We recommend that you use AWS S3 or something similar.
</Note>

```sh theme={null}
helm repo add localstack https://localstack.github.io/helm-charts
helm install localstack localstack/localstack --values=- <<EOF
extraEnvVars:
- name: SERVICES
  value: "s3"
enableStartupScripts: true
startupScriptContent: |
  #!/bin/bash
  awslocal s3 mb s3://juicefs
  awslocal s3 mb s3://logs
persistence:
  enabled: true
  storageClass: local-path
  accessModes:
  - ReadWriteOnce
  size: 50Gi
EOF
```

## Install Helm Chart

Install the helm chart and open connections to the service.

```sh theme={null}
# Step 1: Install the chart
helm install beta9 oci://public.ecr.aws/n4e0e1y0/beta9-chart --version 0.1.166

# Step 2: Confirm the pods are running
kubectl get pods -w

# Step 3: Open ports to the http and grpc services
kubectl port-forward svc/beta9-gateway 1993 1994
```

## Configure the CLI

Create a new config.

```sh theme={null}
./beta9
=> Welcome to Beta9! Let's get started 📡

           ,#@@&&&&&&&&&@&/
        @&&&&&&&&&&&&&&&&&&&&@#
         *@&&&&&&&&&&&&&&&&&&&&&@/
   ##      /&&&&&&&&&&&&&@&&&&&&&&@,
  @&&&&&.    (&&&&&&@/    &&&&&&&&&&/
 &&&&&&&&&@*   %&@.      @& ,@&&&&&&&,
.@&&&&&&&&&&&&#        &&*  ,@&&&&&&&&
*&&&&&&&&&&&@,   %&@/@&*    @&&&&&&&&@
.@&&&&&&&&&*      *&@     .@&&&&&&&&&&
 %&&&&&&&&     /@@*     .@&&&&&&&&&&@,
  &&&&&&&/.#@&&.     .&&&    %&&&&&@,
   /&&&&&&&@%*,,*#@&&(         ,@&&
     /&&&&&&&&&&&&&&,
        #@&&&&&&&&&&,
            ,(&@@&&&,

Context Name [default]:
Gateway Host [0.0.0.0]:
Gateway Port [1993]:
Token:
Added new context 🎉!
```

Confirm the config was created and has a token set.

```sh theme={null}
cat ~/.beta9/config.ini
[default]
token = <token should be here>
gateway_host = localhost
gateway_port = 1993
```

---

> To find navigation and other pages in this documentation, fetch the llms.txt file at: https://docs.beam.cloud/llms.txt
