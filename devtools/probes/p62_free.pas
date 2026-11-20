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

function FindP(B: Variant; Nm: String): Variant;
var It, G: Variant;
begin
  Result := Nil;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  while G <> Nil do begin if G.Name = Nm then Result := G; G := It.NextPCBObject; end;
  B.BoardIterator_Destroy(It);
end;

procedure Body;
var D, B, It, O: Variant; Lst: TInterfaceList; i, best: Integer; P: String;
begin
  P := '%OUT%\batch\One-Air-Max\One-Air-Max.PcbDoc';
  D := Client.OpenDocument('PCB', P);
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', P);
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  Lst := TInterfaceList.Create;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do begin Lst.Add(O); O := It.NextPCBObject; end;
  B.BoardIterator_Destroy(It);
  O := Lst.Items[0];
  L('first ' + O.Name + ' saved: ' + Regions(O) + ' poured=' + BoolToStr(O.Poured, True));
  Pour(O); L(' plain pour: ' + Regions(O));
  O.FreePrimitives; L(' freed: ' + Regions(O) + ' poured=' + BoolToStr(O.Poured, True));
  Pour(O); L(' pour after free: ' + Regions(O));
  O.Poured := True; Pour(O); L(' pour after Poured:=True: ' + Regions(O));
  D.SetModified(False);
end;
