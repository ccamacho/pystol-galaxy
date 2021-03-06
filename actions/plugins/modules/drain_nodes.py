import random
from ansible.module_utils.basic import AnsibleModule
from kubernetes import client
from kubernetes.client.rest import ApiException

from ansible_collections.pystol.actions.plugins.module_utils.k8s_common import load_kubernetes_config

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: drain_nodes

short_description: A module that drain nodes

version_added: "2.8"

description:
    - "A module that drain nodes"

options:
    names:
        default: default
    amount:
        default: 10

author:
    - Carlos Camacho
'''

EXAMPLES = '''
# Pass in a message
- name: Test with a message
  drain_nodes:
    names: ["minikube", "worker1"]
    amount: 3
'''

RETURN = '''
fact:
    description: Actual facts
    type: str
    sample: Jane Doe is a smart cookie.
'''

FACTS = [
    "{name} is looking great today!",
    "{name} is a smart cookie.",
    "I’d choose {name}'s company over pizza anytime."
]


def evict_pod(name, namespace):
    core_v1 = client.CoreV1Api()
    body = kubernetes.client.V1beta1Eviction() # V1beta1Eviction
    try:
        api_response = core_v1.create_namespaced_pod_eviction(
            name=name,
            namespace=namespace,
            body=body)
        pprint(api_response)
    except ApiException as e:
        print("CoreV1Api->create_namespaced_pod_eviction: %s\n" % e)


def cordon_node(name):
    core_v1 = client.CoreV1Api()
    body = {
        "spec": {
            "unschedulable": True
        }
    }
    try:
        api_response = core_v1.patch_node(
            name=name,
            body=body)
        pprint(api_response)
    except ApiException as e:
        print("CoreV1Api->cordon_node: %s\n" % e)


def uncordon_node(name):
    core_v1 = client.CoreV1Api()
    body = {
        "spec": {
            "unschedulable": False
        }
    }
    try:
        api_response = core_v1.patch_node(
            name=name,
            body=body)
        pprint(api_response)
    except ApiException as e:
        print("CoreV1Api->uncordon_node: %s\n" % e)

def drain_node(node_name):
    ret = v1.list_pod_for_all_namespaces(
        field_selector="spec.nodeName={}".format(node_name))

    if not ret.items:
        continue

    # following the drain command from kubectl as best as we can
    eviction_candidates = []
    for pod in ret.items:
        name = pod.metadata.name
        phase = pod.status.phase
        volumes = pod.spec.volumes
        annotations = pod.metadata.annotations

        # do not handle mirror pods
        if annotations and "kubernetes.io/config.mirror" in annotations:
            logger.debug("Not deleting mirror pod '{}' on "
                         "node '{}'".format(name, node_name))
            continue

        if any(filter(lambda v: v.empty_dir is not None, volumes)):
            logger.debug(
                "Pod '{}' on node '{}' has a volume made "
                "of a local storage".format(name, node_name))
            if not delete_pods_with_local_storage:
                logger.debug("Not evicting a pod with local storage")
                continue
            logger.debug("Deleting anyway due to flag")
            eviction_candidates.append(pod)
            continue

        if phase in ["Succeeded", "Failed"]:
            eviction_candidates.append(pod)
            continue

        for owner in pod.metadata.owner_references:
            if owner.controller and owner.kind != "DaemonSet":
                eviction_candidates.append(pod)
                break
            elif owner.kind == "DaemonSet":
                logger.debug(
                    "Pod '{}' on node '{}' is owned by a DaemonSet. Will "
                    "not evict it".format(name, node_name))
                break
        else:
            raise ActivityFailed(
                "Pod '{}' on node '{}' is unmanaged, cannot drain this "
                "node. Delete it manually first?".format(name, node_name))

    if not eviction_candidates:
        logger.debug("No pods to evict. Let's return.")
        return True

    logger.debug("Found {} pods to evict".format(len(eviction_candidates)))
    for pod in eviction_candidates:
        eviction = client.V1beta1Eviction()

        eviction.metadata = client.V1ObjectMeta()
        eviction.metadata.name = pod.metadata.name
        eviction.metadata.namespace = pod.metadata.namespace

        eviction.delete_options = client.V1DeleteOptions()
        try:
            v1.create_namespaced_pod_eviction(
                pod.metadata.name, pod.metadata.namespace, body=eviction)
        except ApiException as x:
            raise ActivityFailed(
                "Failed to evict pod {}: {}".format(
                    pod.metadata.name, x.body))

    pods = eviction_candidates[:]
    started = time.time()
    while True:
        logger.debug("Waiting for {} pods to go".format(len(pods)))
        if time.time() - started > timeout:
            remaining_pods = "\n".join([p.metadata.name for p in pods])
            raise ActivityFailed(
                "Draining nodes did not completed within {}s. "
                "Remaining pods are:\n{}".format(timeout, remaining_pods))

        pending_pods = pods[:]
        for pod in pods:
            try:
                p = v1.read_namespaced_pod(
                    pod.metadata.name, pod.metadata.namespace)
                # rescheduled elsewhere?
                if p.metadata.uid != pod.metadata.uid:
                    pending_pods.remove(pod)
                    continue
                logger.debug("Pod '{}' still around in phase: {}".format(
                    p.metadata.name, p.status.phase))
            except ApiException as x:
                if x.status == 404:
                    # gone...
                    pending_pods.remove(pod)
        pods = pending_pods[:]
        if not pods:
            logger.debug("Evicted all pods we could")
            break

        time.sleep(10)

    return True


def get_pods(namespace=''):
    api_instance = client.CoreV1Api()
    try:
        if namespace == '':
            api_response = api_instance.list_pod_for_all_namespaces()
        else:
            api_response = api_instance.list_namespaced_pod(
                namespace,
                field_selector='status.phase=Running')
        return api_response
    except ApiException as e:
        print("CoreV1Api->list_pod_for_all_namespaces: %s\n" % e)


def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        names=dict(type='str', required=True),
        amount=dict(type='int', required=True),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    out = ""
    err = ""
    rc = 0

    namespace = module.params['namespace']
    amount = module.params['amount']

    result = dict(
        changed=True,
        stdout=out,
        stderr=err,
        rc=rc,
    )

    result['fact'] = random.choice(FACTS).format(
        name=module.params['namespace']
    )

    if module.check_mode:
        return result

    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
