function Regions(G: Variant): String;
var GI, R: Variant; n: Integer; A: Double;
begin
  n := 0; A := 0;
  GI := G.GroupIterator_Create;
  GI.AddFilter_ObjectSet(MkSet(eRegionObject, eTrackObject, eArcObject));
  R := GI.FirstPCBObject;
  while R <> Nil do begin n := n + 1; if R.ObjectId = eRegionObject then A := A + R.Area; R := GI.NextPCBObject; end;
  G.GroupIterator_Destroy(GI);
  Result := IntToStr(n) + ' prims, area mm2=' + FloatToStr(A / 1e8 * 0.0254 * 0.0254);
end;

procedure Pour(G: Variant);
begin
  G.SetState_CopperPourInvalid;
  G.Rebuild;
  G.SetState_CopperPourValid;
end;

procedure Body;
var D, B, It, G: Variant; P: String; i: Integer;
begin
  P := '%OUT%\batch\One-Air-Max\One-Air-Max.PcbDoc';
  D := Client.OpenDocument('PCB', P);
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', P);
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  while (G <> Nil) and (G.Name <> 'BAT1_FCu') do G := It.NextPCBObject;
  B.BoardIterator_Destroy(It);
  L('pts=' + IntToStr(G.PointCount) + ' poured=' + BoolToStr(G.Poured, True) + ' type=' + IntToStr(G.PolygonType) + ' arcmode=' + IntToStr(G.ArcPourMode) + ' border=' + IntToStr(G.BorderWidth) + ' expand=' + IntToStr(G.ExpandOutline) + ' ignoreViol=' + BoolToStr(G.IgnoreViolations, True));
  for i := 0 to G.PointCount do
    L('  seg ' + IntToStr(i) + ' kind=' + IntToStr(G.Segments[i].Kind) + ' ' + IntToStr(G.Segments[i].vx) + ',' + IntToStr(G.Segments[i].vy));
  L('as saved: ' + Regions(G));
  Pour(G); L('repoured: ' + Regions(G));
  G.RemoveDead := False; G.RemoveIslandsByArea := False; Pour(G); L('no dead/islands: ' + Regions(G));
  G.PolyHatchStyle := ePolyHatch90; G.TrackSize := MilsToCoord(10); G.Grid := MilsToCoord(20); Pour(G); L('hatched: ' + Regions(G));
  D.SetModified(False);
end;
