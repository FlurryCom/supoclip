import { fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import { server } from "@/test/setup";

import { FramingSetting } from "./framing-editor";

const channel = { id: "c1", channel: "streamer", cam_box: null, game_box: null };

function frame() {
  server.use(http.get("/api/live/channels/c1/framing-frame", () =>
    HttpResponse.json({ image: "data:image/jpeg;base64,AAAA", at: "2026-11-22T22:00:00Z" })));
}

it("saves a channel without a webcam as a game box alone", async () => {
  frame();
  const onChange = vi.fn();
  render(<FramingSetting channel={channel} onChange={onChange} />);
  expect(screen.getByText(/Automatic/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Mark webcam and game/ }));
  await screen.findByAltText("Recent frame of streamer's live");
  fireEvent.click(screen.getByRole("switch", { name: "No webcam" }));
  fireEvent.click(screen.getByRole("button", { name: "Use this framing" }));
  expect(onChange).toHaveBeenCalledWith({ cam_box: null, game_box: { x: 0, y: 0, w: 1, h: 1 } });
});

it("moves the webcam box with the arrow keys and keeps it inside the frame", async () => {
  frame();
  const onChange = vi.fn();
  render(<FramingSetting channel={{ ...channel, cam_box: { x: 0.7, y: 0.02, w: 0.28, h: 0.36 }, game_box: { x: 0, y: 0, w: 1, h: 1 } }} onChange={onChange} />);
  expect(screen.getByText("Marked by hand: webcam and game.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Mark webcam and game/ }));
  const cam = await screen.findByRole("group", { name: /Webcam box/ });
  for (let i = 0; i < 5; i++) fireEvent.keyDown(cam, { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("button", { name: "Use this framing" }));
  expect(onChange).toHaveBeenCalledWith({ cam_box: { x: 0.72, y: 0.02, w: 0.28, h: 0.36 }, game_box: { x: 0, y: 0, w: 1, h: 1 } });
});

it("says when there is no frame yet", async () => {
  server.use(http.get("/api/live/channels/c1/framing-frame", () => HttpResponse.json({ detail: "No frame yet" }, { status: 404 })));
  render(<FramingSetting channel={channel} onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Mark webcam and game/ }));
  expect(await screen.findByText(/The frame shows up after/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Use this framing" })).toBeDisabled();
});

it("lets a drag on the image reach the drawing when it starts over the other box", async () => {
  frame();
  render(<FramingSetting channel={channel} onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Mark webcam and game/ }));
  const game = await screen.findByRole("group", { name: /Game box/ });
  expect(game).toHaveClass("pointer-events-none"); // the webcam is the box being drawn
  fireEvent.click(screen.getByRole("button", { name: "Game", pressed: false }));
  expect(game).not.toHaveClass("pointer-events-none");
});

it("resizes the focused box with shift and the arrow keys", async () => {
  frame();
  const onChange = vi.fn();
  render(<FramingSetting channel={{ ...channel, cam_box: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 }, game_box: { x: 0, y: 0, w: 1, h: 1 } }} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /Mark webcam and game/ }));
  const cam = await screen.findByRole("group", { name: /Webcam box/ });
  fireEvent.keyDown(cam, { key: "ArrowRight", shiftKey: true });
  fireEvent.keyDown(cam, { key: "ArrowDown", shiftKey: true });
  fireEvent.click(screen.getByRole("button", { name: "Use this framing" }));
  expect(onChange).toHaveBeenCalledWith({ cam_box: { x: 0.1, y: 0.1, w: 0.21, h: 0.21 }, game_box: { x: 0, y: 0, w: 1, h: 1 } });
});

it("shows the automatic change with undo and keep", () => {
  const onChange = vi.fn();
  const now = { x: 0.7, y: 0.02, w: 0.28, h: 0.36 };
  const before = { cam_box: null, game_box: null };
  render(<FramingSetting
    channel={{ ...channel, cam_box: now, game_box: { x: 0, y: 0, w: 1, h: 1 }, framing_changed_at: "2026-11-22T22:00:00Z", framing_notice: "changed", framing_reason: "A webcam mudou de canto.", framing_previous: before }}
    onChange={onChange}
  />);
  expect(screen.getByText(/adjusted automatically/)).toBeInTheDocument();
  expect(screen.getByText(/A webcam mudou de canto/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(onChange).toHaveBeenLastCalledWith({ ...before, framing_auto: false }); // undo also stops automatic changes
  fireEvent.click(screen.getByRole("button", { name: "It's right" }));
  expect(onChange).toHaveBeenLastCalledWith({ cam_box: now, game_box: { x: 0, y: 0, w: 1, h: 1 } });
});

it("a warning without a fix has no undo", () => {
  render(<FramingSetting channel={{ ...channel, framing_changed_at: "2026-11-22T22:00:00Z", framing_notice: "attention", framing_reason: "Não achei a webcam.", framing_previous: null }} onChange={vi.fn()} />);
  expect(screen.getByText(/needs attention/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  expect(screen.getByRole("button", { name: "Review" })).toBeInTheDocument();
});

it("turns automatic changes off and on", () => {
  const onChange = vi.fn();
  render(<FramingSetting channel={{ ...channel, framing_auto: false }} onChange={onChange} />);
  expect(screen.getByText(/only warns/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("switch", { name: "Adjust by itself when the layout changes" }));
  expect(onChange).toHaveBeenCalledWith({ framing_auto: true });
});
