"""Helper to load resume state (epoch, best val loss, history, checkpoint) for fine-tuning."""
import json
import math
from pathlib import Path
import torch


def _dedupe_history(history):
    """Keep the last record for each epoch while preserving epoch order."""
    by_epoch = {}
    for entry in history:
        try:
            epoch = int(entry.get("epoch", 0))
        except Exception:
            continue
        if epoch > 0:
            by_epoch[epoch] = entry
    return [by_epoch[epoch] for epoch in sorted(by_epoch)]


def _infer_no_improve(history, best_val_loss):
    """Infer early-stopping patience state from de-duplicated history."""
    no_improve = 0
    best = float("inf")
    for entry in _dedupe_history(history):
        if "val_loss" not in entry:
            continue
        try:
            val_loss = float(entry["val_loss"])
        except Exception:
            continue
        if val_loss < best:
            best = val_loss
            no_improve = 0
        else:
            no_improve += 1
    if math.isfinite(best_val_loss) and math.isfinite(best):
        return no_improve
    return 0


def load_resume_state(output_dir, model, device, reset_from_best=False):
    """Load resume state from output_dir.

    Returns (start_epoch, start_step, best_val_loss, no_improve, history, resume_source).
    - start_epoch : first epoch to run (1-based)
    - start_step  : number of batches already processed in start_epoch (0 = begin of epoch)
    - resume_source: "latest.pt", "best.pt", or None
    Side effect: loads model weights from latest.pt (preferred) or best.pt if present.
    Set reset_from_best=True only for an explicit fresh run from best.pt; this ignores latest.pt.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    history_path = output_dir / "training_history.json"
    best_path    = output_dir / "best.pt"
    latest_path  = output_dir / "latest.pt"

    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            history = _dedupe_history(history)
        except Exception as e:
            print(f"  ! Failed to read history: {e}; starting fresh history")
            history = []

    best_val_loss = float("inf")
    if best_path.exists():
        try:
            ckpt = torch.load(best_path, map_location=device, weights_only=True)
            if isinstance(ckpt, dict) and "val_loss" in ckpt:
                best_val_loss = float(ckpt["val_loss"])
        except Exception as e:
            print(f"  ! Failed to read best.pt val_loss: {e}")

    ckpt_to_load = None
    if latest_path.exists() and not reset_from_best:
        ckpt_to_load = latest_path
    elif best_path.exists():
        ckpt_to_load = best_path

    last_complete_epoch = 0
    start_step          = 0
    no_improve          = 0
    resume_source       = ckpt_to_load.name if ckpt_to_load is not None else None

    if ckpt_to_load is not None:
        try:
            ckpt = torch.load(ckpt_to_load, map_location=device, weights_only=True)
            if isinstance(ckpt, dict) and "model_state_dict" in ckpt:
                model.load_state_dict(ckpt["model_state_dict"])
                epoch_in_ckpt = int(ckpt.get("epoch", 0))
                step_in_ckpt  = int(ckpt.get("step_in_epoch", 0))
                no_improve    = int(ckpt.get("no_improve", 0))
                if "best_val_loss" in ckpt:
                    best_val_loss = float(ckpt["best_val_loss"])

                if step_in_ckpt > 0:
                    # Mid-epoch checkpoint: epoch_in_ckpt is not yet complete
                    last_complete_epoch = epoch_in_ckpt - 1
                    start_step          = step_in_ckpt
                    print(
                        f"  Loaded mid-epoch checkpoint from {ckpt_to_load.name}"
                        f" (epoch={epoch_in_ckpt}, skip_steps={start_step})"
                    )
                else:
                    last_complete_epoch = epoch_in_ckpt
                    print(
                        f"  Loaded epoch-end checkpoint from {ckpt_to_load.name}"
                        f" (epoch={epoch_in_ckpt} complete)"
                    )
            else:
                print(f"  ! {ckpt_to_load.name} has no model_state_dict; skipping load")
        except Exception as e:
            print(f"  ! Failed to load {ckpt_to_load.name}: {e}")

    if last_complete_epoch == 0 and start_step == 0 and history:
        try:
            last_complete_epoch = max(int(h.get("epoch", 0)) for h in history)
        except Exception:
            last_complete_epoch = 0

    start_epoch = last_complete_epoch + 1

    if not math.isfinite(best_val_loss) and history:
        try:
            best_val_loss = min(float(h["val_loss"]) for h in history if "val_loss" in h)
        except Exception:
            pass
    if no_improve == 0:
        no_improve = _infer_no_improve(history, best_val_loss)

    print(f"  resume_source  = {resume_source}")
    print(f"  start_epoch    = {start_epoch}")
    print(f"  start_step     = {start_step}")
    print(f"  best_val_loss  = {best_val_loss}")
    print(f"  no_improve     = {no_improve}")
    print(f"  history entries = {len(history)}")
    return start_epoch, start_step, best_val_loss, no_improve, history, resume_source


def restore_optimizer_state(output_dir, optimizer, scheduler, scaler, device, total_steps=None):
    """Restore optimizer / scheduler / scaler states from latest.pt if available.

    Call this AFTER load_resume_state and AFTER creating optimizer/scheduler/scaler,
    when resuming from latest.pt. If total_steps is provided, scheduler.T_max is
    corrected to the current run's absolute training horizon after loading state.
    """
    output_dir  = Path(output_dir)
    latest_path = output_dir / "latest.pt"
    if not latest_path.exists():
        return
    try:
        ckpt = torch.load(latest_path, map_location=device, weights_only=True)
        if isinstance(ckpt, dict):
            if "optimizer_state_dict" in ckpt:
                optimizer.load_state_dict(ckpt["optimizer_state_dict"])
                print("  Restored optimizer state")
            if "scheduler_state_dict" in ckpt:
                scheduler.load_state_dict(ckpt["scheduler_state_dict"])
                if total_steps is not None and hasattr(scheduler, "T_max"):
                    scheduler.T_max = int(total_steps)
                print("  Restored scheduler state")
            if "scaler_state_dict" in ckpt:
                scaler.load_state_dict(ckpt["scaler_state_dict"])
                print("  Restored scaler state")
            lr = scheduler.get_last_lr()[0] if hasattr(scheduler, "get_last_lr") else None
            t_max = getattr(scheduler, "T_max", None)
            last_epoch = getattr(scheduler, "last_epoch", None)
            print(f"  Scheduler check: lr={lr}, T_max={t_max}, last_epoch={last_epoch}")
    except Exception as e:
        print(f"  ! Failed to restore optimizer state: {e}")
