# comfy_parser.py made by nenya
import json

# --- Constants ---
COMFY_METADATA_PROPAGATE_NONE = True # If a node required for propagation is None, stop propagation

# --- Data Structures ---
comfy_nodes_propagation_data = [
    # ... (Keep the entire list from the original code here) ...
    {
        "class_type": 'TagSeparator',
        "mapping": {
            0: "pos_prompt",
            1: "neg_prompt",
        }
    },
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'ModelSamplingWaifuDiffusionV',
                'Mahiro',
                'ModelSamplingFlux',
                'IPAdapterUnifiedLoader',
                'IPAdapterAdvanced',
                'IPAdapter',
                'ApplyFluxIPAdapter',
                'ApplyAdvancedFluxIPAdapter',
                'IPAdapterAdvanced',
            ],
        },
        "mapping": {
            0: "model",
        }
    },
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'ModelMergeSimple',
                'ModelMergeAdd',
                'ModelMergeSubstract',
            ],
        },
        "mapping": {
            0: {
                "operation_type": "format",
                "keys_to_use": ["model1", "model2"],
                "operation_input": "{model1} [+] {model2}",
            },
        }
    },
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'CheckpointLoaderSimple',
                'Checkpoint Loader',
            ],
        },
        "mapping": {
            0: "ckpt_name",
        }
    },
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'UnetLoaderGGUF',
                'UNETLoader',
                'UnetLoaderGGUFAdvanced',
            ],
        },
        "mapping": {
            0: "unet_name"
        }
    },
    {
        "class_type": 'CLIPTextEncode',
        "mapping": {
            0: "text",
            1: "clip",
        }
    },
    {
        "class_type": 'Seed',
        "mapping": {
            0: 'seed',
        }
    },
    {
        "class_type": 'KSampler',
        "mapping": {
            0: 'latent_image',
        }
    },
    {
        "class_type": 'VAEEncode',
        "mapping": {
            0: 'pixels',
        }
    },
    {
        "class_type": 'LatentBlend',
        "mapping": {
            0: 'samples1',
        }
    },
    {
        "class_type": 'VAEDecode',
        "mapping": {
            0: 'samples',
        }
    },
    {
        "class_type": 'ImageBlend',
        "mapping": {
            0: 'image1',
        }
    },
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'ImageScaleBy',
                'ImageUpscaleWithModel',
            ],
        },
        "mapping": {
            0: 'image',
        }
    },
    {
        "class_type": 'EmptyLatentImage',
        "mapping": {
            0: {
                "operation_type": "format",
                "keys_to_use": ["width", "height"],
                "operation_input": "{width} x {height}",
            },
        }
    },
    {
        "class_type": 'LoraLoader',
        "mapping": {
            0: {
                "operation_type": "format",
                "keys_to_use": ["model", "lora_name", 'strength_model'],
                "operation_input": "{model}\n+ LoRA: <{lora_name}:{strength_model}>",
            }
        }
    },
]

target_comfy_nodes = [
    # ... (Keep the entire list from the original code here) ...
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'KSampler',
                'KSampler (WAS)',
            ]
        },
        "inputs": [
            "model",
            "positive",
            "negative",
            "latent_image",
            "sampler_name",
            "scheduler",
            "cfg",
            "steps",
            "seed",
        ]
    },
    {
        "class_type": {
            "operation_type": "any_of_inputs",
            "operation_input": [
                'KSamplerAdvanced',
            ]
        },
        "inputs": [
            "model",
            "positive",
            "negative",
            "latent_image",
            "sampler_name",
            "scheduler",
            "cfg",
            "steps",
            "noise_seed",
        ]
    },
]

format_of_comfy_fields_to_types = {
    # ... (Keep the entire dict from the original code here) ...
    'models': ['{model}'],
    'pos_prompts': ['{positive}'],
    'neg_prompts': ['{negative}'],
    'img_gen_sizes': ['{latent_image}'],
    'sampler_configs': ['{sampler_name} @ {scheduler} @ cfg: {cfg:.2f} @ {steps} steps'],
    'seeds': ['{seed}', '{noise_seed}'],
}

comfy_fields_pretty_names = {
    # ... (Keep the entire dict from the original code here) ...
    'models': "Model",
    'pos_prompts': "Prompt",
    'neg_prompts': "Negative Prompt",
    'img_gen_sizes': "Size",
    'sampler_configs': "Sampler Config",
    'seeds': "Seed",
}

# --- Helper Functions ---
def custom_operation(operation_data, input_object):
    """Performs custom operations defined in the mapping data."""
    op_type = operation_data.get('operation_type')
    op_input = operation_data.get('operation_input')

    if op_type == "any_of_inputs":
        return input_object in op_input
    elif op_type == "format":
        format_str = op_input
        # Ensure all keys exist, providing a default if necessary
        keys_to_use = operation_data.get('keys_to_use', [])
        format_args = {key: input_object.get(key, f"{{{key}}}") for key in keys_to_use}
        try:
            return format_str.format(**format_args)
        except KeyError as e:
            print(f"Warning: Missing key for formatting: {e}")
            return format_str # Return unformatted string on error
    elif op_type == "caseless_contains":
        return isinstance(input_object, str) and op_input.lower() in input_object.lower()
    else:
        print(f"Warning: Unknown custom operation type: {op_type}")
        return None # Or raise an error

def resolve_class_type(node_type, list_of_formats):
    """Finds the matching format definition for a given node class type."""
    for node_format in list_of_formats:
        class_type_def = node_format['class_type']
        if isinstance(class_type_def, str):
            if class_type_def == node_type:
                return node_format
        elif isinstance(class_type_def, dict): # Custom operation
            if custom_operation(class_type_def, node_type):
                return node_format
        else:
            print(f"Warning: Unknown class_type format: {class_type_def}")

    return None

def is_comfy_link(obj):
    """Checks if an object represents a ComfyUI node link."""
    return isinstance(obj, list) and len(obj) == 2 and isinstance(obj[0], str) and isinstance(obj[1], int)

def resolve_bypasses(comfy_link, workflow_data):
    """Recursively resolves links through bypass/passthrough nodes."""
    if comfy_link is None:
        return None

    if not is_comfy_link(comfy_link):
        return comfy_link # Value is not a link, return as is

    linked_node_id = comfy_link[0]
    linked_node_input_id = comfy_link[1]

    # Check if the linked node exists in the workflow data
    if linked_node_id not in workflow_data:
        # print(f"Warning: Linked node ID '{linked_node_id}' not found in workflow data.")
        return f"Error: Missing node {linked_node_id}" # Indicate missing node

    linked_node = workflow_data[linked_node_id]
    if not linked_node or 'class_type' not in linked_node:
        # print(f"Warning: Invalid linked node data for ID '{linked_node_id}'.")
        return f"Error: Invalid node {linked_node_id}" # Indicate invalid node data

    linked_node_type = linked_node['class_type']

    # Find if this node type is defined for propagation
    propagation_rule = resolve_class_type(linked_node_type, comfy_nodes_propagation_data)
    if propagation_rule is None:
        # This node type doesn't propagate, so we stop here (or maybe return an identifier?)
        # Depending on desired behavior, you might return None or something else.
        # For now, returning None as it signifies the end of this propagation path.
        return None

    mapping = propagation_rule.get('mapping', {})
    # Check if the specific input ID has a mapping rule
    if linked_node_input_id not in mapping:
        # print(f"Warning: No mapping found for input ID {linked_node_input_id} in node type '{linked_node_type}'.")
        return None # No rule for this specific output of the node

    mapping_result = mapping[linked_node_input_id]

    if isinstance(mapping_result, str): # Simple key mapping
        input_key_to_follow = mapping_result
        if input_key_to_follow not in linked_node.get('inputs', {}):
            # print(f"Warning: Mapped input key '{input_key_to_follow}' not found in node '{linked_node_id}'.")
            return None # The required input doesn't exist on the node
        new_link = linked_node['inputs'][input_key_to_follow]
        return resolve_bypasses(new_link, workflow_data) # Recurse

    elif isinstance(mapping_result, dict): # Custom operation (like formatting)
        resolved_keys = {}
        keys_to_use = mapping_result.get('keys_to_use', [])
        if not keys_to_use:
            print(f"Warning: Formatting rule found for node type '{linked_node_type}' but no 'keys_to_use' defined.")
            return None

        for key in keys_to_use:
            if key not in linked_node.get('inputs', {}):
                print(f"Warning: Key '{key}' needed for formatting not found in inputs of node '{linked_node_id}'.")
                if COMFY_METADATA_PROPAGATE_NONE:
                    return None
                resolved_keys[key] = f"{{{key}}}" # Use placeholder if not propagating None
                continue # Skip resolving this key

            resolved_value = resolve_bypasses(linked_node['inputs'][key], workflow_data)
            if COMFY_METADATA_PROPAGATE_NONE and resolved_value is None:
                return None # Stop propagation if any required key resolves to None
            resolved_keys[key] = resolved_value if resolved_value is not None else f"{{{key}}}" # Use placeholder if None

        # Perform the custom operation (e.g., formatting)
        return custom_operation(mapping_result, resolved_keys)

    else:
        print(f"Warning: Unknown mapping result type for node type '{linked_node_type}': {mapping_result}")
        return None

# --- Main Parsing Function ---
def comfyui_get_data(workflow_json_str: str) -> dict:
    """
    Tries to extract key parameters (prompts, models, seeds, etc.) from ComfyUI
    workflow metadata embedded in a PNG's info field.

    Args:
        workflow_json_str: The JSON string containing the ComfyUI workflow.

    Returns:
        A dictionary containing the extracted parameters.
    """
    extracted_params = []
    try:
        workflow_data = json.loads(workflow_json_str)
        if not isinstance(workflow_data, dict):
            print("Warning: ComfyUI data is not a JSON object.")
            return [] # Expecting workflow to be a JSON object (dict)

        target_node_instances = {}
        # First pass: Identify all instances of target node types
        for node_id, node_details in workflow_data.items():
            if node_details and 'class_type' in node_details:
                target_format = resolve_class_type(node_details['class_type'], target_comfy_nodes)
                if target_format is not None:
                    # Store the node details along with the inputs we need to resolve
                    target_node_instances[node_id] = {
                        'details': node_details,
                        'required_inputs': target_format.get('inputs', []),
                        'resolved_params': {} # Initialize dict to store resolved values
                    }

        # Second pass: Resolve inputs for the identified target nodes
        for node_id, node_info in target_node_instances.items():
            node_details = node_info['details']
            node_inputs = node_details.get('inputs', {})
            for input_key in node_info['required_inputs']:
                if input_key in node_inputs:
                    # Resolve the value for this input, tracing back through links
                    resolved_value = resolve_bypasses(node_inputs[input_key], workflow_data)
                    # Store the final resolved value (even if it's None or an error string)
                    node_info['resolved_params'][input_key] = resolved_value
                # else:
                    # Optionally handle cases where a required input is missing entirely
                    # node_info['resolved_params'][input_key] = f"Error: Missing input {input_key}"

        # Third pass: Format the resolved parameters according to predefined rules
        results_by_type = {key: [] for key in format_of_comfy_fields_to_types}
        for node_id, node_info in target_node_instances.items():
            resolved_params = node_info['resolved_params']
            for field_type, format_strings in format_of_comfy_fields_to_types.items():
                for format_str in format_strings:
                    try:
                        # Attempt to format using the resolved parameters
                        formatted_value = format_str.format(**resolved_params)
                        # Add only if formatting was successful (no missing keys)
                        if formatted_value not in results_by_type[field_type]: # Avoid duplicates
                            results_by_type[field_type].append(formatted_value)
                    except KeyError:
                        # Ignore if a key required for this specific format is missing
                        pass
                    except TypeError as e:
                        # Catch potential type errors during formatting (e.g. float format on non-float)
                        print(f"Warning: Formatting error for {field_type} ('{format_str}') with params {resolved_params}: {e}")
                        pass

        # Final pass: Convert the grouped results into the desired output format
        for field_type, values in results_by_type.items():
            pretty_name = comfy_fields_pretty_names.get(field_type, field_type.replace('_', ' ').title())
            for value in values:
                # Truncate long values before appending
                val_str = str(value)
                if len(val_str) > 1023:
                    val_str = val_str[:1020] + "..."
                extracted_params.append({"type": pretty_name, "val": val_str})

        # Fix before passing to PI-Chan
        final = {}
        for param in extracted_params:
            final[param['type']] = param['val']
            
        return final

    except json.JSONDecodeError as e:
        print(f"Error decoding ComfyUI JSON: {e}")
        # Check if it might be InvokeAI metadata mistaken for ComfyUI
        if '"generation_mode":' in workflow_json_str:
            print("Detected potential InvokeAI metadata structure.")
            # You could potentially add InvokeAI parsing here or return a specific indicator
        return []
    except Exception as e:
        import traceback
        print(f"Unexpected error parsing ComfyUI data: {e}")
        traceback.print_exc()
        return []

# --- Example Usage (for testing) ---
if __name__ == '__main__':
    # Add a sample ComfyUI workflow JSON string here for testing
    test_json = """
    {
      "3": {
        "inputs": {
          "seed": 89898989,
          "steps": 20,
          "cfg": 7.0,
          "sampler_name": "dpmpp_2m",
          "scheduler": "karras",
          "denoise": 1.0,
          "model": ["4", 0],
          "positive": ["6", 0],
          "negative": ["7", 0],
          "latent_image": ["5", 0]
        },
        "class_type": "KSampler",
        "_meta": {"title": "KSampler"}
      },
      "4": {
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        "class_type": "CheckpointLoaderSimple",
        "_meta": {"title": "Load Checkpoint"}
      },
      "5": {
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        "class_type": "EmptyLatentImage",
        "_meta": {"title": "Empty Latent Image"}
      },
      "6": {
        "inputs": {
          "text": "beautiful landscape painting, epic composition",
          "clip": ["4", 1]
        },
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "CLIP Text Encode (Prompt)"}
      },
      "7": {
        "inputs": {"text": "ugly, deformed", "clip": ["4", 1]},
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "CLIP Text Encode (Negative)"}
      }
    }
    """
    parsed_data = comfyui_get_data(test_json)
    print(json.dumps(parsed_data, indent=2))
    # Expected output (order might vary):
    # [
    #   { "type": "Model", "val": "sd_xl_base_1.0.safetensors" },
    #   { "type": "Prompt", "val": "beautiful landscape painting, epic composition" },
    #   { "type": "Negative Prompt", "val": "ugly, deformed" },
    #   { "type": "Size", "val": "1024 x 1024" },
    #   { "type": "Sampler Config", "val": "dpmpp_2m @ karras @ cfg: 7.00 @ 20 steps" },
    #   { "type": "Seed", "val": "89898989" }
    # ]