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
